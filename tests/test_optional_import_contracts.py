"""Protect retained dependency-import contracts without emulating native success.

Edge cases: absent optional Win32 support, non-import failures, unavailable Pillow
or its compiled core, and corrupt image resources remain distinguishable. The Win32
fault checks execute only the exact import block; they do not qualify Windows startup.
"""
from __future__ import annotations

import ast
import builtins
import io
from pathlib import Path
from types import CodeType

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'src/controller/app_controller.py'
LOADER = ROOT / 'src/model/media_loader.py'


def _win32_block() -> ast.Try:
    """Select the one optional Win32 import boundary, rejecting ambiguity."""
    blocks = [node for node in ast.parse(APP.read_bytes()).body
              if isinstance(node, ast.Try) and any(
                  isinstance(item, ast.Import) and any(
                      alias.name == 'win32api' for alias in item.names)
                  for item in node.body)]
    assert len(blocks) == 1
    return blocks[0]


def _pillow_import() -> ast.ImportFrom:
    """Return the actual unconditional import; no surrogate importer is introduced."""
    nodes = [node for node in ast.parse(LOADER.read_bytes()).body
             if isinstance(node, ast.ImportFrom) and node.module == 'PIL'
             and any(alias.name == 'Image' for alias in node.names)]
    assert len(nodes) == 1
    return nodes[0]


def _code(node: ast.stmt) -> CodeType:
    """Compile the unchanged statement in a test-owned namespace."""
    return compile(ast.Module(body=[node], type_ignores=[]), '<retained-import>', 'exec')


def _failure_globals(error: BaseException) -> dict[str, object]:
    """Inject only failure at import; never supply a fake successful library."""
    def fail_import(name: str, globals: object = None, locals: object = None,
                    fromlist: object = (), level: int = 0) -> object:
        assert name in ('win32api', 'PIL') and level == 0
        raise error
    namespace = vars(builtins).copy()
    namespace['__import__'] = fail_import
    return {'__builtins__': namespace}


@pytest.mark.parametrize('target', ['win32api', 'PIL.Image'])
def test_reviewed_import_is_retained(target: str) -> None:
    """This preservation guard must fail if a reviewed import is removed."""
    if target == 'win32api':
        _win32_block()
    else:
        _pillow_import()


@pytest.mark.parametrize('error_type', [ImportError, ModuleNotFoundError])
def test_win32_import_absence_selects_none(error_type: type[ImportError]) -> None:
    """Preserve the existing ImportError boundary, not a synthetic native module."""
    namespace = _failure_globals(error_type('controlled missing Win32 support'))
    exec(_code(_win32_block()), namespace)
    assert namespace['win32api'] is None


@pytest.mark.parametrize('error_type', [RuntimeError, OSError, ValueError,
                                       KeyboardInterrupt, SystemExit])
def test_win32_other_failures_propagate(error_type: type[BaseException]) -> None:
    error = error_type('controlled non-import failure')
    namespace = _failure_globals(error)
    with pytest.raises(error_type) as captured:
        exec(_code(_win32_block()), namespace)
    assert captured.value is error
    assert 'win32api' not in namespace


def test_win32_absence_handler_is_narrow_and_unchanged() -> None:
    block = _win32_block()
    assert len(block.body) == len(block.handlers) == 1
    assert not block.orelse and not block.finalbody
    handler = block.handlers[0]
    assert isinstance(handler.type, ast.Name) and handler.type.id == 'ImportError'
    assert len(handler.body) == 1
    assignment = handler.body[0]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Constant) and assignment.value.value is None


@pytest.mark.parametrize('error_type', [ImportError, ModuleNotFoundError, OSError,
                                       RuntimeError, KeyboardInterrupt, SystemExit])
def test_pillow_import_failure_is_not_optional(error_type: type[BaseException]) -> None:
    """The source's unguarded Pillow import never fabricates an available Image."""
    error = error_type('controlled unavailable Pillow')
    namespace = _failure_globals(error)
    with pytest.raises(error_type) as captured:
        exec(_code(_pillow_import()), namespace)
    assert captured.value is error
    assert 'Image' not in namespace


def test_retained_image_alias_is_real_provider() -> None:
    from PIL import Image
    from src.model import media_loader
    assert media_loader.Image is Image
    assert Path(Image.core.__file__).is_file()
    assert Image.core.__name__ == 'PIL._imaging'


@pytest.mark.parametrize('image_format', ['PNG', 'BMP', 'TIFF'])
def test_real_pillow_resource_roundtrip(image_format: str) -> None:
    """Exercise real small image resources, not WaveHelm metadata decoding claims."""
    from src.model import media_loader
    with io.BytesIO() as payload:
        with media_loader.Image.new('RGB', (2, 3), (12, 34, 56)) as image:
            image.save(payload, format=image_format)
        payload.seek(0)
        with media_loader.Image.open(payload) as decoded:
            decoded.load()
            assert decoded.size == (2, 3)
            assert decoded.convert('RGB').getpixel((1, 2)) == (12, 34, 56)


@pytest.mark.parametrize('payload', [b'', b'not an image', b'\x89PNG\r\n\x1a\n'])
def test_corrupt_image_is_not_reported_as_success(payload: bytes) -> None:
    from PIL import UnidentifiedImageError
    from src.model import media_loader
    with io.BytesIO(payload) as stream:
        with pytest.raises((UnidentifiedImageError, OSError)):
            media_loader.Image.open(stream)


def test_declared_optional_stack_keeps_exact_existing_pins() -> None:
    requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
    assert 'Pillow==12.3.0' in requirements
    assert 'pywin32==311; platform_system == "Windows"' in requirements


def test_import_names_are_not_read_by_application_functions() -> None:
    """Record lexical absence without treating it as proof of no import effects."""
    for path, name in ((APP, 'win32api'), (LOADER, 'Image')):
        tree = ast.parse(path.read_bytes())
        uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load) and node.id == name]
        assert uses == []
