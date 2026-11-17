"""Fixed Codex IoA assistant entry. Run only with the bound client interpreter."""
from pathlib import Path

from ioalab_codex.native_entry import main

if __name__ == '__main__':
    raise SystemExit(main(Path(__file__).resolve().parent, 'assistant'))
