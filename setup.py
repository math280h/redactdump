from distutils.core import setup

import setuptools

setup(
    name="redactdump",

    version="0.1.0",

    author="math280h",

    packages=setuptools.find_packages(include=['redactdump', 'redactdump.*']),

    include_package_data=True,

    url="https://github.com/math280h/redactdump",

    description="redactdump",

    long_description=open("README.md").read(),

    install_requires=[
        'rich==10.15.2',
        'PyYAML==6.0',
        'schema==0.7.4',
        'configargparse==1.5.3'
    ],

    entry_points={
            'console_scripts': ['redactdump=redactdump.app:start_application']
    }
)