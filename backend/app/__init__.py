from . import envfile as _envfile

_envfile.load()  # .env support; must run before any module reads os.environ
