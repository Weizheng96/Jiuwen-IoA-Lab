"""Minimal OS context for deployment children; business credentials are explicit input."""
import os


OS_ENV = frozenset({'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP',
                   'USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES',
                   'PROGRAMFILES(X86)', 'HOMEDRIVE', 'HOMEPATH', 'PROCESSOR_ARCHITECTURE',
                   'PROCESSOR_ARCHITEW6432', 'NUMBER_OF_PROCESSORS', 'OS', 'USERNAME', 'USER',
                   'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                   'REQUESTS_CA_BUNDLE', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY'})


def child_environment():
    return {**{key:value for key,value in os.environ.items() if key.upper() in OS_ENV},
            'PYTHONUTF8':'1', 'PYTHONNOUSERSITE':'1', 'PIP_CONFIG_FILE':os.devnull}
