import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit


def main():
    import uvicorn
    from .store import Store
    from .coordinator import Coordinator
    from .sandbox import DockerSandbox
    from .gateway import create_app
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--deployment-profile',choices=('linux-server','windows-wsl'))
    args = parser.parse_args()
    config = json.loads(args.config.read_text('utf-8'))
    from .profile import validate_environment
    profile=config.get('deployment_profile')
    if args.deployment_profile and args.deployment_profile!=profile:
        parser.error('Launcher and configuration deployment_profile differ')
    validate_environment(profile)
    origin = urlsplit(config['origin'])
    if origin.scheme != 'https' or origin.path not in ('','/') or origin.query or origin.fragment:
        raise ValueError('A dedicated HTTPS trial origin is required')
    home = Path(config['control_directory']).resolve()
    home.mkdir(parents=True,mode=0o700,exist_ok=True)
    # One writer/coordinator only. Keep an OS-owned lock for the entire app life.
    import fcntl
    with (home/'coordinator.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(home/'control.sqlite',deployment_profile=profile)
        try:
            coordinator = Coordinator(store,audience=config['origin'],admission_key=config['admission_key'],
                lease_seconds=config['lease_seconds'],grace_seconds=config['grace_seconds'])
            sandbox=DockerSandbox(config['sandbox'],deployment_profile=profile)
            sandbox.validate_image()
            app = create_app(coordinator,sandbox,confirm_origin=config['confirm_origin'],formal_origin=config['formal_origin'])
            uvicorn.run(app,host='127.0.0.1',port=config.get('port',8766),proxy_headers=False)
        finally:
            store.close()


if __name__ == '__main__':
    main()
