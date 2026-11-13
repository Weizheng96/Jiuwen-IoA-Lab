"""Role-bound launcher; both skills use the same installed client."""
import subprocess
import sys

CLIENT_PYTHON = __IOALAB_PYTHON__
if __name__ == '__main__':
    raise SystemExit(subprocess.call([CLIENT_PYTHON, '-I', '-X', 'utf8', '-m',
                                     'ioalab_client', '--role', '__IOALAB_ROLE__', *sys.argv[1:]]))
