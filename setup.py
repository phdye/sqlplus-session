"""Minimal setup.py -- stdlib only, Python 3.6.8+."""

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
    long_description_content_type='text/markdown',
    url='https://github.com/phdye/sqlplus-session',
    author='Philip Dye',
    author_email='phdye@acm.org',
    license='MIT',
    packages=find_packages(exclude=['tests']),
    # One floor for the library and the console script. The library
    # passes encoding= and errors= to Popen, which arrived in 3.6, so
    # the 3.2.8 this used to declare was never true.
    python_requires='>=3.6.8',
    entry_points={
        'console_scripts': [
            'sqlrun = sqlplus_session.sqlrun:cli',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Topic :: Database',
    ],
)
