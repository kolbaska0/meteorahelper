import subprocess
import os
from datetime import datetime
import zipfile
import re
from pathlib import Path


def get_current_version():
    """Read the current version from const.py"""
    with open("const.py", "r") as f:
        content = f.read()
        match = re.search(r'VERSION = "(\d+\.\d+\.\d+)"', content)
        if match:
            return match.group(1)
    raise ValueError("Version not found in const.py")


def increment_version(version):
    """Increment patch version by 0.0.1"""
    major, minor, patch = map(int, version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def update_version_file(new_version):
    """Update version in const.py"""
    with open("const.py", "r") as f:
        content = f.read()

    new_content = re.sub(
        r'VERSION = "\d+\.\d+\.\d+"', f'VERSION = "{new_version}"', content
    )

    with open("const.py", "w") as f:
        f.write(new_content)


def check_version_changed():
    """Check if version was changed in the last commit"""
    result = subprocess.run(
        ["git", "diff", "HEAD^", "HEAD", "--", "const.py"],
        capture_output=True,
        text=True,
    )
    return "VERSION" in result.stdout


def main():
    current_version = get_current_version()

    if not check_version_changed():
        new_version = increment_version(current_version)
        print(f"Updating version from {current_version} to {new_version}")

        update_version_file(new_version)

        subprocess.run(["git", "add", "const.py"])
        subprocess.run(["git", "commit", "--amend", "--no-edit"])

        current_version = new_version

    print("Building application with PyInstaller...")
    subprocess.run(
        [
            "pyinstaller",
            "--onefile",
            "--windowed",
            "LiquiDMonAppUI.py",
            "--icon",
            "icon.ico",
            "--add-data",
            "icon.ico;.",
            "--name",
            "MeteoraHelper",
        ]
    )

    dist_path = Path("dist")
    zip_name = dist_path / f"MeteoraHelper-{current_version}.zip"
    print(f"Creating archive {zip_name}...")

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zipf:
        for exe_file in ["MeteoraHelper.exe", "server.exe"]:
            exe_path = dist_path / exe_file
            if exe_path.exists():
                zipf.write(exe_path, exe_file)
            else:
                print(f"Warning: {exe_file} not found in dist folder")

    print(f"Build and archive creation completed successfully: {zip_name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {str(e)}")
        exit(1)
