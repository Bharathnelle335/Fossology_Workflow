# app.py — FOSSology UI with docker/git/upload, tag/branch picker, and run tracking
import re
import json
import time
import uuid
import requests
import streamlit as st

# ---------------- CONFIG ---------------- #
OWNER = "Bharathnelle335"
REPO = "Fossology_Workflow"
WORKFLOW_FILE = "fossology_E2E_with_tags_input.yml"  # must match .github/workflows/<file>.yml
BRANCH = "main"
TOKEN = st.secrets["GITHUB_TOKEN"]  # fine-grained/classic token with workflow: write + actions: read

BASE_API = "https://api.github.com"
HEADERS = {
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

def normalize_git(url: str, ref_input: str):
    """Return (git_url_ending_dot_git, resolved_ref, meta dict)."""
    meta = {"parsed_from_url": False, "owner": None, "repo": None, "detected_ref": None}
    repo_url_git = (url or "").strip()
    detected_ref = None

    m = GH_RE.match(repo_url_git)
    if m:
        owner = m.group("owner"); repo = m.group("repo")
        meta.update(owner=owner, repo=repo)
        detected_ref = m.group("ref")
        if not repo_url_git.endswith(".git"):
            repo_url_git = f"https://github.com/{owner}/{repo}.git"
        if detected_ref:
            meta["parsed_from_url"] = True
            meta["detected_ref"] = detected_ref

    repo_ref = (ref_input or "").strip() or (detected_ref or "main")
    return repo_url_git, repo_ref, meta

def gh_get(url, **params):
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {url} -> {r.status_code} {r.text}")
    return r.json()

def fetch_refs(owner: str, repo: str, per_page: int = 100):
    tags = gh_get(f"{BASE_API}/repos/{owner}/{repo}/tags", per_page=per_page)
    branches = gh_get(f"{BASE_API}/repos/{owner}/{repo}/branches", per_page=per_page)
    tag_names = [t["name"] for t in tags] if isinstance(tags, list) else []
    branch_names = [b["name"] for b in branches] if isinstance(branches, list) else []
    # Build a single pick-list with prefixes so users know what they chose
    options = [f"tag: {t}" for t in tag_names] + [f"branch: {b}" for b in branch_names]
    # Prefer common defaults on top
    options_sorted = list(dict.fromkeys(
        ([o for o in options if o.endswith(" main")] +
         [o for o in options if o.endswith(" master")] +
         options)
    ))
    return options_sorted

def dispatch_workflow(inputs: dict):
    url = f"{BASE_API}/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    return requests.post(url, headers=HEADERS, json={"ref": BRANCH, "inputs": inputs}, timeout=30)

def find_run_by_token(token: str, timeout_s: int = 60):
    """Find the newly-created run by matching the run_name/display_title containing client_run_id."""
    start = time.time()
    while time.time() - start < timeout_s:
        data = gh_get(f"{BASE_API}/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs",
                      event="workflow_dispatch", per_page=50)
        runs = data.get("workflow_runs", [])
        for r in runs:
            title = r.get("display_title") or r.get("name") or ""
            if token in title:
                return r  # contains id, html_url, status, conclusion
        time.sleep(3)
    return None

def poll_run_status(run_id: int, max_seconds: int = 300):
    start = time.time()
    while time.time() - start < max_seconds:
        r = gh_get(f"{BASE_API}/repos/{OWNER}/{REPO}/actions/runs/{run_id}")
        status, concl = r.get("status"), r.get("conclusion")
        yield status, concl, r
        if status in ("completed",):
            return
        time.sleep(5)

# ---------------- Inputs ---------------- #
scan_type = st.radio("Scan type", ["docker", "git", "upload-zip", "upload-tar"], horizontal=True)

docker_image = ""
git_url_raw = ""
git_ref_in = ""
archive_url = ""

if scan_type == "docker":
    docker_image = st.text_input("Docker image", "alpine:latest",
                                 help="Example: nginx:1.25.5 or alpine:latest")

elif scan_type == "git":
    colg1, colg2 = st.columns([3, 2])
    with colg1:
        git_url_raw = st.text_input("Git URL",
                                    "https://github.com/psf/requests/tree/v2.32.3",
                                    help="Any GitHub repo; /tree/<tag> /commit/<sha> /releases/tag/<tag> supported.")
    with colg2:
        git_ref_in = st.text_input("Git ref (optional)", "",
                                   help="Branch / tag / commit. Leave blank if URL already encodes it.")

    # Normalize + optional ref loader
    norm_git_url, norm_git_ref, meta = normalize_git(git_url_raw, git_ref_in)
    with st.expander("🔎 Normalization preview", expanded=False):
        st.write("**Repo URL (normalized):**", norm_git_url or "—")
        st.write("**Ref (resolved):**", norm_git_ref or "—")
        if meta["parsed_from_url"]:
            st.info(f"Detected ref `{meta['detected_ref']}` from the pasted URL.")

    if meta["owner"] and meta["repo"] and st.button("📥 Load tags & branches"):
        try:
            choices = fetch_refs(meta["owner"], meta["repo"])
            pick = st.selectbox("Pick a tag/branch", choices, key="pick_ref")
            if pick:
                # Overwrite git_ref_in with picked value
                if pick.startswith("tag: "):
                    st.session_state["git_ref_final"] = pick.replace("tag: ", "", 1)
                elif pick.startswith("branch: "):
                    st.session_state["git_ref_final"] = pick.replace("branch: ", "", 1)
        except Exception as e:
            st.error(f"Failed to fetch refs: {e}")

elif scan_type in ("upload-zip", "upload-tar"):
    placeholder = "https://example.com/source.tar.gz" if scan_type == "upload-tar" else "https://example.com/source.zip"
    archive_url = st.text_input("Archive URL", placeholder,
                                help="Public URL to .zip or .tar(.gz/.xz/.tgz/.txz). Workflow will download it.")

client_run_id = st.text_input("Run tag (optional)", "",
                              help="Used to find your run & artifacts. If empty, a random tag will be generated.")

# ---------------- Submit ---------------- #
if st.button("🚀 Start scan"):
    # Resolve final git ref (if any was picked)
    if scan_type == "git":
        norm_git_url, norm_git_ref, meta = normalize_git(git_url_raw, git_ref_in)
        if "git_ref_final" in st.session_state:
            norm_git_ref = st.session_state["git_ref_final"]
        if not norm_git_url.lower().startswith("https://github.com/"):
            st.error("❌ Provide a valid GitHub URL.")
            st.stop()

    # Make sure we have a token tag to find the run
    token_tag = (client_run_id or f"ui-{uuid.uuid4().hex[:8]}").strip()

    # Compose workflow inputs (must match YAML)
    inputs = {
        "scan_type": scan_type,
        "docker_image": docker_image.strip() if scan_type == "docker" else "",
        "git_url": norm_git_url if scan_type == "git" else "",
        "git_ref": norm_git_ref if scan_type == "git" else "",
        "archive_url": archive_url.strip() if scan_type in ("upload-zip", "upload-tar") else "",
        "client_run_id": token_tag,
    }

    try:
        resp = dispatch_workflow(inputs)
        if resp.status_code == 204:
            st.success("✅ Workflow dispatched.")
            st.markdown("**Inputs used:**")
            st.code(json.dumps(inputs, indent=2))

            with st.status("Looking up the workflow run…", expanded=True) as status:
                run = find_run_by_token(token_tag, timeout_s=90)
                if not run:
                    st.warning("Could not locate the run yet. Open the Actions tab below to view progress.")
                    st.link_button("Open GitHub Actions", f"https://github.com/{OWNER}/{REPO}/actions", type="primary")
                else:
                    run_id = run["id"]
                    run_url = run["html_url"]
                    st.success(f"Found run: {run_id}")
                    st.link_button("Open run in GitHub", run_url, type="primary")

                    # Live status polling
                    placeholder = st.empty()
                    for status_str, conclusion, run_obj in poll_run_status(run_id, max_seconds=600):
                        placeholder.info(f"Status: **{status_str}** • Conclusion: **{conclusion or '—'}**")
                        if status_str == "completed":
                            break

                    # Final artifact hint
                    st.markdown(
                        f"📦 When finished, artifacts appear at the bottom of the run page:\n\n"
                        f"- **fossology_report.csv**\n"
                        f"- **fossology_report.xlsx**\n\n"
                        f"[Open run]({run_url})"
                    )
        else:
            st.error(f"❌ Dispatch failed ({resp.status_code})")
            st.code(resp.text or "<no response body>")
    except Exception as e:
        st.exception(e)
