import setuptools

with open("README.md", "r") as f:
    long_description = f.read()

setuptools.setup(
    name="slackmoji-uploader",
    version="0.2.0",
    author="shareef12",
    author_email="shareef12@twelvetacos.com",
    description="Upload emojis from slackmojis.com to a Slack workspace",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/shareef12/slackmoji-uploader",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.5",
    entry_points={
        "console_scripts": [
            "slackmoji-uploader=slackmoji_uploader.upload:main",
        ]
    })