import streamlit as st
import requests

# ---------------- CONFIG ---------------- #
OWNER = "Bharathnelle335"
REPO = "Fossology_Workflow"
WORKFLOW_FILE = "fossology_E2E.yml"
BRANCH = "main"
TOKEN = st.secrets["GITHUB_TOKEN"]

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json"
}

st.title("🧩 Fossology Scan Runner")

# ---------------- Inputs ---------------- #
scan_type = st.radio("Select Scan Type", ["docker", "repo"], horizontal=True)
docker_image = st.text_input("Docker image (if docker)", "alpine:latest")
repo_url = st.text_input("Repo URL (if repo)", "https://github.com/example/repo.git")

st.markdown("### Select Agents")

# Render checkboxes horizontally
cols = st.columns(3)
agents = {
    "nomos": cols[0].checkbox("nomos", True),
    "ojo": cols[1].checkbox("ojo"),
    "monk": cols[2].checkbox("monk", True),
    "copyright": cols[0].checkbox("copyright"),
    "keyword": cols[1].checkbox("keyword"),
    "pkgagent": cols[2].checkbox("pkgagent"),
}

# ---------------- Trigger Workflow ---------------- #
if st.button("🚀 Start Scan"):
    inputs = {
        "scan_type": scan_type,
        "docker_image": docker_image,
        "repo_url": repo_url,
        **{f"agent_{k}": str(v).lower() for k, v in agents.items()},
    }

    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    resp = requests.post(url, headers=headers, json={"ref": BRANCH, "inputs": inputs})

    if resp.status_code == 204:
        st.success("✅ Scan initiated!")

        # Format selected agents as horizontal chips
        selected = [a for a, enabled in agents.items() if enabled]
        st.markdown(
            "### Selected Agents\n" +
            " ".join([f"`{a}`" for a in selected])
        )

        st.info(f"🔗 [View results in GitHub Actions](https://github.com/{OWNER}/{REPO}/actions)")
    else:
        st.error(f"❌ Failed: {resp.status_code} {resp.text}")
