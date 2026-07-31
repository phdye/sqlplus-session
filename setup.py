"""Minimal setup.py -- stdlib only, Python 3.2.5+."""

from setuptools import setup, find_packages

setup(
    name='sqlplus-session',
    version='0.1.0',
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
