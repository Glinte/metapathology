project('meson-python-871-repro', version: '0.0.0')

py = import('python').find_installation()
py.install_sources(
  'src/repro_pkg/__init__.py',
  'src/repro_pkg/probe.py',
  subdir: 'repro_pkg',
)
