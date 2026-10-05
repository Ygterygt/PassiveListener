"""Create missing output ancestors privately, without repairing existing ACLs."""

from contextlib import ExitStack
from pathlib import Path

from passivelistener.configuration import local_path
from passivelistener.private_storage import private_directory
from passivelistener.storage_handles import directory_lease


def provision_output(root: Path) -> None:
    local_path(str(root))
    with ExitStack() as stack:
        stack.enter_context(directory_lease(Path(root.anchor)))
        # Existing shared ancestors are held, not modified. Newly created ones
        # receive the private DACL before they become visible in the namespace.
        for parent in reversed(root.parents[:-1]):
            if parent.exists():
                stack.enter_context(directory_lease(parent))
            else:
                stack.enter_context(private_directory(parent, create=True))
        stack.enter_context(private_directory(root, create=True))
