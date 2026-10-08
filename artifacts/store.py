import json
from pathlib import Path

from evidence.pack import EvidencePack


class ArtifactStore:
    def __init__(self, root: str = "run_artifacts") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_evidence_pack(self, pack: EvidencePack) -> Path:
        run_dir = self.root / pack.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "evidence-pack.json"
        path.write_text(
            json.dumps(pack.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def load_evidence_pack(self, run_id: str) -> EvidencePack:
        path = self.root / run_id / "evidence-pack.json"
        return EvidencePack.model_validate(json.loads(path.read_text(encoding="utf-8")))
