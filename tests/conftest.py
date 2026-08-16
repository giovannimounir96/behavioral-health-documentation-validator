from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def sample_payload(project_root: Path) -> dict[str, object]:
    appointments = pd.read_csv(
        project_root / "data/sample/appointments.csv",
        dtype=str,
        keep_default_na=False,
    )
    documentation = pd.read_csv(
        project_root / "data/sample/documentation.csv",
        dtype=str,
        keep_default_na=False,
    )
    return {
        "source": "pytest",
        "appointments": appointments.to_dict(orient="records"),
        "documentation": documentation.to_dict(orient="records"),
    }
