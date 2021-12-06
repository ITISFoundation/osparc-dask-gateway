pytest_plugins = [
    "pytest_simcore.repository_paths",
]

import sys
from pathlib import Path

import osparc_gateway_server
import pytest

CURRENT_DIR = Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve().parent
WILDCARD = "services/osparc-gateway-server/README.md"
ROOT = Path("/")


@pytest.fixture(scope="session")
def package_dir():
    pdir = Path(osparc_gateway_server.__file__).resolve().parent
    assert pdir.exists()
    return pdir


@pytest.fixture(scope="session")
def osparc_gateway_server_root_dir(request) -> Path:
    """osparc-simcore repo root dir"""
    test_dir = Path(request.session.fspath)  # expected test dir in simcore

    root_dir = CURRENT_DIR
    for start_dir in (CURRENT_DIR, test_dir):
        root_dir = start_dir
        while not any(root_dir.glob(WILDCARD)) and root_dir != ROOT:
            root_dir = root_dir.parent

        if root_dir != ROOT:
            break

    msg = (
        f"'{root_dir}' does not look like the git root directory of osparc-dask-gateway"
    )

    assert root_dir != ROOT, msg
    assert root_dir.exists(), msg
    assert any(root_dir.glob(WILDCARD)), msg
    assert any(root_dir.glob(".git")), msg

    return root_dir


@pytest.fixture(scope="session")
def pylintrc(osparc_gateway_server_root_dir: Path) -> Path:
    pylintrc = osparc_gateway_server_root_dir / ".pylintrc"
    assert pylintrc.exists()
    return pylintrc
