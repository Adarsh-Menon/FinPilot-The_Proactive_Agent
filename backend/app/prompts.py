import hashlib
import json
import re
from pathlib import Path

ROLES = {"coordinator", "categorizer", "investigator", "analyzer", "forecaster", "advisor", "risk", "goals"}


def sha(data):
    """SHA-256 hex digest of bytes.

    Args:
        data: bytes to hash.

    Returns:
        64-character hex string.

    Used by: PromptCatalog checksums.
    """
    return hashlib.sha256(data).hexdigest()


class PromptCatalog:
    """Versioned prompt releases with checksums; loads the selected release's prompt for an agent.

    Used by: The model gateway for every agent call; Admin > Overview shows the selected release.
    """
    def __init__(self, root: Path):
        """Point the catalog at the prompts folder.

        Args:
            root: path to `prompts/`.
        """
        self.root = root.resolve()

    def _read(self, path):
        """Read a file inside the catalog, refusing paths that escape it.

        Args:
            path: path relative to the catalog root.

        Returns:
            File bytes.
        """
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Prompt path escapes catalog")
        return target.read_bytes()

    def selected(self):
        """Verify the whole release history and return the selected release.

        Checks every release's parent hash, roles, template paths and checksums, so an edited prompt cannot be used
        silently.

        Returns:
            The selected release dict plus its release_sha256.

        Used by: compose() and Admin > Overview.
        """
        pointer = json.loads(self._read("manifest.json"))
        version = pointer.get("selected_release", "")
        if not re.fullmatch(r"v[1-9][0-9]*", version) or pointer.get("release_path") != f"releases/{version}.json":
            raise ValueError("Invalid prompt pointer")
        previous, previous_hash = None, None
        selected = None
        files = sorted((p for p in (self.root / "releases").glob("v*.json") if re.fullmatch(r"v[1-9][0-9]*", p.stem)), key=lambda p: int(p.stem[1:]))
        for file in files:
            raw = self._read(f"releases/{file.name}")
            release = json.loads(raw)
            if release.get("schema_version") != 1 or release.get("release_version") != file.stem:
                raise ValueError("Invalid release schema")
            if release.get("parent_release") != previous or release.get("parent_sha256") != previous_hash:
                raise ValueError("Broken prompt history")
            if set(release.get("templates", {})) != ROLES | {"common"}:
                raise ValueError("Incomplete prompt release")
            for role, entry in release["templates"].items():
                if not re.fullmatch(r"v[1-9][0-9]*", entry["version"]) or entry["path"] != f"{role}/{entry['version']}.md":
                    raise ValueError("Invalid template path")
                if sha(self._read(entry["path"])) != entry["sha256"]:
                    raise ValueError("Prompt checksum mismatch")
            previous, previous_hash = file.stem, sha(raw)
            if file.stem == version:
                if previous_hash != pointer["release_sha256"]:
                    raise ValueError("Release checksum mismatch")
                selected = {**release, "release_sha256": previous_hash}
        if selected is None:
            raise ValueError("Unknown prompt release")
        return selected

    def compose(self, agent):
        """The shared and agent prompts joined, with provenance for tracing.

        Args:
            agent: role name, e.g. 'advisor'.

        Returns:
            (prompt text, provenance dict with release and prompt versions and hashes).

        Used by: Every model call in mesh.py.
        """
        if agent not in ROLES:
            raise ValueError("Unknown agent")
        release = self.selected()
        common, role = release["templates"]["common"], release["templates"][agent]
        # Reverify the actual bytes being composed, protecting against intervening edits.
        texts = []
        for entry in (common, role):
            raw = self._read(entry["path"])
            if sha(raw) != entry["sha256"]:
                raise ValueError("Prompt changed during loading")
            texts.append(raw.decode("utf-8"))
        return "\n\n".join(texts), {"release_version": release["release_version"],
            "release_sha256": release["release_sha256"], "common_version": common["version"],
            "common_sha256": common["sha256"], "agent": agent, "prompt_version": role["version"], "prompt_sha256": role["sha256"]}
