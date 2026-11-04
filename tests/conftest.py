import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from _pdfbackend import available_backends  # noqa: E402

BACKENDS = available_backends()


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    from samples import build_all

    return build_all(str(tmp_path_factory.mktemp("pdfs")))


@pytest.fixture(params=BACKENDS or [pytest.param(None, marks=pytest.mark.skip("无 PDF 后端"))])
def backend(request):
    return request.param
