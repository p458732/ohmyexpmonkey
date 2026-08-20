import os

from setuptools import setup, find_packages

_here = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(_here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    # Distribution name differs from the import name on purpose: the project is
    # ohmyexpmonkey, but training scripts written for classic expmonkey keep
    # doing `from expmonkey import get_branch`, so the package stays expmonkey.
    name='ohmyexpmonkey',
    version='0.1.0',
    description='Branch-based experiment management for the agent era '
                '(a continuation of expmonkey)',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/p458732/ohmyexpmonkey',
    packages=find_packages(),
    # gitignore.template is not a .py file, so it needs listing explicitly or a
    # non-editable install ships without it.
    package_data={'expmonkey': ['gitignore.template']},
    scripts=[
        'scripts/em-init.sh',
        'scripts/em-completion.zsh',
    ],
    entry_points={
        'console_scripts': [
            'em=expmonkey:main',
            'expmonkey=expmonkey:main',
        ],
    },
    python_requires='>=3.8',
)
