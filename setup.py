from distutils.core import setup

import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="redactdump",

    version="0.3.1",

    author="math280h",

    packages=setuptools.find_packages(include=['redactdump', 'redactdump.*']),

    include_package_data=True,

    url="https://github.com/math280h/redactdump",

    description="redactdump",

    long_description=long_description,
    long_description_content_type="text/markdown",

    install_requires=[
        'rich==10.15.2',
        'PyYAML==6.0',
        'schema==0.7.5',
        'configargparse==1.5.3',
        'SQLAlchemy~=1.4.27',
        'psycopg2-binary==2.9.3',
        'pymysql==1.0.2',
        'faker==10.0.0'
    ],

    entry_points={
            'console_scripts': ['redactdump=redactdump.app:start_application']
    }
)
