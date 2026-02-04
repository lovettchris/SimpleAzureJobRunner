import json
import os
import subprocess


def find_az_cmd():
    for path in os.environ["PATH"].split(os.pathsep):
        az = os.path.join(path, "az.cmd")
        if os.path.exists(az):
            return az
    raise Exception("az.cmd not found in PATH")


if os.name == "nt":
    az = find_az_cmd()
else:
    az = "az"


def run_az_cmd(cmd: str, description: str, no_data_ok: bool = False):
    print(description)
    result = subprocess.run(f"{az} {cmd}", capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise Exception(f"Error {description}: {result.stderr}")

    else:
        if result.stdout:
            data = json.loads(result.stdout)
            return data
        elif not no_data_ok:
            raise Exception(f"No json data returned: {result.stderr}")
        return {}
