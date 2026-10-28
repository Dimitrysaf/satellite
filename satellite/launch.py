"""Start Satellite's companion apps (Messages, Contacts)."""

import os
import shutil
import subprocess
import sys


def launch(executable, module, *arguments):
    """Run the installed launcher, or the module from a source checkout."""
    path = shutil.which(executable)
    if path:
        command = [path, *arguments]
        env = None
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isdir(os.path.join(root, module.split(".")[0])):
            return False
        command = [sys.executable, "-m", module, *arguments]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(
            p for p in (root, os.environ.get("PYTHONPATH")) if p))
    try:
        subprocess.Popen(command, env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True
