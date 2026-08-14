"""Minimal setup.py -- stdlib only, Python 3.2.5+."""

import re

from setuptools import setup, find_packages


def read_version():
    """Take the version from __init__.py rather than repeating it here.

    Two bumps in a single day, with the string written out in two
    places, is enough evidence that they will drift.
    """
    with open('sqlplus_session/__init__.py') as fh:
        m = re.search(r"^__version__\s*=\s*'([^']+)'", fh.read(), re.M)
    if not m:
        raise RuntimeError('no __version__ in sqlplus_session/__init__.py')
    return m.group(1)


setup(
    name='sqlplus-session',
    version=read_version(),
    description='Persistent Oracle sqlplus session over pipes',
    long_description=open('README.md').read(),
    author='Philip Dye',
    author_email='phdye@acm.org',
    license='MIT',
    packages=find_packages(exclude=['tests']),
    python_requires='>=3.2',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Topic :: Database',
    ],
)
