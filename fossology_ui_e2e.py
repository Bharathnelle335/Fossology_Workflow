import re
import streamlit as st
import requests

# ---------------- CONFIG ---------------- #
OWNER = "Bharathnelle335"
REPO = "Fossology_Workflow"
WORKFLOW_FILE = "fossology_E2E_with_tags_input.yml"
BRANCH = "main"
TOKEN = st.secrets["GITHUB_TOKEN"]

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json"
}

st.title("🧩 Fossology Scan Runner")

# ---------------- Helpers ---------------- #
GH_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/\.#]+)(?:\.git)?"
    r"(?:/(?:tree|commit|releases/tag)/(?P<ref>[^/?#]+))?",
    re.IGNORECASE,
)

def normalize_repo(url: str, ref_input: str) -> tuple[str, str, dict]:
    """
    Return (repo_url_git, repo_ref, meta)
    - Ensures https://github.com/<owner>/<repo>.git
    - Extracts ref if user pasted a web URL (tree/tag/commit).
    - Prefers explicit ref_input if provided (non-empty).
    - meta: info dict for UI hints.
    """
    meta = {"parsed_from_url": False, "owner": None, "repo": None, "detected_ref": None}
    m = GH_RE.match(url.strip())
    repo_url_git = url.strip()
    detected_ref = None

    if m:
        owner = m.group("owner")
        repo = m.group("repo")
        meta["owner"], meta["repo"] = owner, repo
        detected_ref = m.group("ref")
        if not repo_url_git.endswith(".git"):
            repo_url_git = f"https://github.com/{owner}/{repo}.git"
        if detected_ref:
            meta["parsed_from_url"] = True
            meta["detected_ref"] = detected_ref

    # Choose final ref: explicit input wins; else detected; else "main"
    repo_ref = (ref_input or "").strip() or (detected_ref or "main")
    return repo_url_git, repo_ref, meta

def selected_agents_dict(flags: dict) -> dict:
    # Convert bools to "true"/"false" (strings) for workflow inputs
    return {f"agent_{k}": str(v).lower() for k, v in flags.items()}

# ---------------- Inputs ---------------- #
scan_type = st.radio("Select Scan Type", ["docker", "repo"], horizontal=True)

col1, col2 = st.columns([3, 2])
with col1:
    docker_image = st.text_input("Docker image (if docker)", "alpine:latest")
with col2:
    st.caption("Add a tag like `nginx:1.25.5` to pin an image version.")

repo_url_raw = st.text_input("Repo URL (if repo)", "https://github.com/example/repo.git")
repo_ref_input = st.text_input(
    "Repo ref (branch / tag / commit) (if repo)",
    "",
    help="Examples: main, v1.2.3, 1a2b3c4. Leave empty if your pasted URL already has /tree/<ref> or /releases/tag/<tag>."
)

st.markdown("### Select Agents")
cols = st.columns(3)
agents = {
    "nomos": cols[0].checkbox("nomos", True),
    "ojo": cols[1].checkbox("ojo"),
    "monk": cols[2].checkbox("monk", True),
    "copyright": cols[0].checkbox("copyright"),
    "keyword": cols[1].checkbox("keyword"),
    "pkgagent": cols[2].checkbox("pkgagent"),
}

# Live normalization preview (for user confidence)
norm_repo_url, norm_repo_ref, meta = normalize_repo(repo_url_raw, repo_ref_input)
with st.expander("🔎 Input normalization preview", expanded=False):
    st.write("**Repo URL (normalized):**", norm_repo_url)
    st.write("**Repo ref (resolved):**", norm_repo_ref)
    if meta["parsed_from_url"]:
        st.info(f"Detected ref `{meta['detected_ref']}` from the pasted URL.")

# ---------------- Trigger Workflow ---------------- #
if st.button("🚀 Start Scan"):
    # Basic validation
    if scan_type == "repo":
        if not norm_repo_url.lower().startswith("https://github.com/") or "/github.com/" not in f"/{norm_repo_url}":
            st.error("❌ Please provide a valid GitHub repository URL.")
            st.stop()
        if not norm_repo_url.endswith(".git"):
            st.error("❌ Repo URL must point to the .git endpoint (auto-normalization should have handled this).")
            st.stop()

    inputs = {
        "scan_type": scan_type,
        "docker_image": docker_image,
        "repo_url": norm_repo_url,
        "repo_ref": norm_repo_ref,  # NEW
        **selected_agents_dict(agents),
    }

    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    resp = requests.post(url, headers=headers, json={"ref": BRANCH, "inputs": inputs})

    if resp.status_code == 204:
        st.success("✅ Scan initiated!")

        # Format selected agents as horizontal chips
        selected = [a for a, enabled in agents.items() if enabled]
        st.markdown("### Selected Agents\n" + " ".join([f"`{a}`" for a in selected]))

        # Helpful recap
        if scan_type == "repo":
            st.markdown(f"**Repo:** `{norm_repo_url}`  \n**Ref:** `{norm_repo_ref}`")
        else:
            st.markdown(f"**Docker image:** `{docker_image}`")

        st.info(f"🔗 [View results in GitHub Actions](https://github.com/{OWNER}/{REPO}/actions)")
    else:
        st.error(f"❌ Failed: {resp.status_code} {resp.text}")
