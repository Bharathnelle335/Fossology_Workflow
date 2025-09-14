import re
import json
import requests
import streamlit as st

# ---------------- CONFIG ---------------- #
OWNER = "Bharathnelle335"
REPO = "Fossology_Workflow"
WORKFLOW_FILE = "fossology_E2E_with_tags_input.yml"  # must match the YAML filename in .github/workflows/
BRANCH = "main"
TOKEN = st.secrets["GITHUB_TOKEN"]  # add a classic fine-grained token with 'workflows:write' on this repo

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json"
}

st.set_page_config(page_title="FOSSology Scan Runner", page_icon="🧩", layout="centered")
st.title("🧩 FOSSology Scan Runner")

# ---------------- Helpers ---------------- #
GH_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/\.#]+)(?:\.git)?"
    r"(?:/(?:tree|commit|releases/tag)/(?P<ref>[^/?#]+))?",
    re.IGNORECASE,
)

def normalize_git(url: str, ref_input: str) -> tuple[str, str, dict]:
    """Return (git_url_ending_dot_git, repo_ref, meta)."""
    meta = {"parsed_from_url": False, "owner": None, "repo": None, "detected_ref": None}
    repo_url_git = (url or "").strip()
    detected_ref = None

    m = GH_RE.match(repo_url_git)
    if m:
        owner = m.group("owner"); repo = m.group("repo")
        meta["owner"], meta["repo"] = owner, repo
        detected_ref = m.group("ref")
        if not repo_url_git.endswith(".git"):
            repo_url_git = f"https://github.com/{owner}/{repo}.git"
        if detected_ref:
            meta["parsed_from_url"] = True
            meta["detected_ref"] = detected_ref

    repo_ref = (ref_input or "").strip() or (detected_ref or "main")
    return repo_url_git, repo_ref, meta

def dispatch_workflow(inputs: dict) -> requests.Response:
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    return requests.post(url, headers=headers, json={"ref": BRANCH, "inputs": inputs})

# ---------------- UI ---------------- #
scan_type = st.radio(
    "Scan type",
    ["docker", "git", "upload-zip", "upload-tar"],
    horizontal=True
)

with st.container():
    if scan_type == "docker":
        docker_image = st.text_input("Docker image", "alpine:latest", help="e.g., nginx:1.25.5 or alpine:latest")
        git_url_raw = git_ref = ""  # unused
        archive_url = ""            # unused
    elif scan_type == "git":
        colg1, colg2 = st.columns([3, 2])
        with colg1:
            git_url_raw = st.text_input("Git URL", "https://github.com/psf/requests/tree/v2.32.3")
        with colg2:
            git_ref = st.text_input("Git ref (branch / tag / commit)", "", help="Leave blank if the URL already has /tree/<ref> or /releases/tag/<tag>")
        docker_image = ""           # unused
        archive_url = ""            # unused
    else:
        docker_image = ""           # unused
        git_url_raw = git_ref = ""  # unused
        placeholder = "https://example.com/source.tar.gz" if scan_type == "upload-tar" else "https://example.com/source.zip"
        archive_url = st.text_input("Archive URL", placeholder, help="Public URL to .zip or .tar(.gz/.xz/.tgz/.txz)")

client_run_id = st.text_input("Run tag (optional)", "", help="Opaque tag to help you find artifacts later (defaults to run_id).")

# Live preview for Git normalization
if scan_type == "git" and git_url_raw:
    norm_git_url, norm_git_ref, meta = normalize_git(git_url_raw, git_ref)
    with st.expander("🔎 Normalization preview", expanded=False):
        st.write("**Repo URL (normalized):**", norm_git_url)
        st.write("**Ref (resolved):**", norm_git_ref)
        if meta["parsed_from_url"]:
            st.info(f"Detected ref `{meta['detected_ref']}` from the pasted URL.")

# ---------------- Submit ---------------- #
if st.button("🚀 Start FOSSology scan"):
    # Basic validation + normalization
    if scan_type == "docker":
        if not docker_image.strip():
            st.error("❌ Please enter a Docker image (e.g., alpine:latest).")
            st.stop()
        inputs = {
            "scan_type": "docker",
            "docker_image": docker_image.strip(),
            "git_url": "",
            "git_ref": "",
            "archive_url": "",
            "client_run_id": client_run_id.strip(),
        }

    elif scan_type == "git":
        norm_git_url, norm_git_ref, _ = normalize_git(git_url_raw, git_ref)
        if not norm_git_url.lower().startswith("https://github.com/"):
            st.error("❌ Provide a valid GitHub URL (owner/repo).")
            st.stop()
        inputs = {
            "scan_type": "git",
            "docker_image": "",
            "git_url": norm_git_url,
            "git_ref": norm_git_ref,
            "archive_url": "",
            "client_run_id": client_run_id.strip(),
        }

    else:  # upload-zip / upload-tar
        if not archive_url.strip():
            st.error("❌ Provide an archive_url to a ZIP/TAR file.")
            st.stop()
        inputs = {
            "scan_type": scan_type,
            "docker_image": "",
            "git_url": "",
            "git_ref": "",
            "archive_url": archive_url.strip(),
            "client_run_id": client_run_id.strip(),
        }

    # Fire the workflow
    resp = dispatch_workflow(inputs)
    if resp.status_code == 204:
        st.success("✅ Scan initiated.")
        st.markdown("**Inputs used:**")
        st.code(json.dumps(inputs, indent=2))
        st.link_button("Open GitHub Actions runs", f"https://github.com/{OWNER}/{REPO}/actions", type="primary")
    else:
        st.error(f"❌ Failed to dispatch ({resp.status_code})")
        st.code(resp.text or "<no body>")
