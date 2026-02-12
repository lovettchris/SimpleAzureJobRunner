import time
from typing import Any, Dict

from azure.core.credentials import TokenCredential
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.mgmt.compute import ComputeManagementClient
from ioutils import get_exception_info
from logger import Logger

logger = Logger()
log = logger.get_root_logger()
AZURE_VM_RUNNING_MSG = "VM guest agent was detected running"
DEFAULT_UNAVAILABLE_TIMEOUT = 600


class AzureVmState:
    """This class represents the state of an Azure VM."""

    DEALLOCATED = "PowerState/deallocated"
    DEALLOCATING = "PowerState/deallocating"
    RUNNING = "PowerState/running"
    STARTING = "PowerState/starting"
    STOPPED = "PowerState/stopped"
    KNOWN_STATES = [
        DEALLOCATED,
        DEALLOCATING,
        RUNNING,
        STARTING,
        STOPPED,
    ]

    def __init__(self, name: str, state: str, is_linux: bool, is_windows: bool):
        self.name = name
        self.state = state
        self.is_linux = is_linux
        self.is_windows = is_windows
        self.identity: Any | None = None
        self.subscription_id: str = ""
        self.resource_group: str = ""
        assert self.is_linux != self.is_windows, "is_linux and is_windows are mutually exclusive!"
        self.starting = False
        self.deallocating = False
        self.start_time = 0.0
        self.deallocate_time = 0.0
        if state == AzureVmState.STARTING:
            self.starting = True
            self.start_time = time.time()
        elif state == AzureVmState.DEALLOCATING:
            self.deallocating = True
            self.deallocate_time = time.time()

    @property
    def vm_type(self) -> str:
        """Return the type of VM as a string."""
        if self.is_linux:
            return "Linux"
        elif self.is_windows:
            return "Windows11"
        else:
            raise ValueError("Unknown VM type!")

    def is_unknown_state(self) -> bool:
        return self.state not in AzureVmState.KNOWN_STATES

    def is_starting_or_running(self) -> bool:
        return self.is_running() or self.state == AzureVmState.STARTING

    def is_running(self) -> bool:
        return self.state == AzureVmState.RUNNING

    def is_deallocated(self) -> bool:
        return (
            self.state == AzureVmState.DEALLOCATING
            or self.state == AzureVmState.DEALLOCATED
            or self.state == AzureVmState.STOPPED
        )

    def on_start(self) -> None:
        """Record the fact that we are starting this VM so that we don't try and deallocate it."""
        self.starting = True
        self.start_time = time.time()
        self.state = AzureVmState.STARTING
        self.deallocating = False

    def on_deallocate(self) -> None:
        """Record the fact that we are deallocating this machine."""
        self.starting = False
        self.state = AzureVmState.DEALLOCATING
        self.deallocating = True
        self.deallocate_time = time.time()


class AzureVms:
    def __init__(
        self,
        subscription_id: str,
        resource_group: str,
        managed_identity: str = None,
        unavailable_timeout: int = DEFAULT_UNAVAILABLE_TIMEOUT,
    ):
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.managed_identity = managed_identity
        self.unavailable_timeout = unavailable_timeout
        self.unavailable_retry_time = 0.0
        self.unavailable_state = False
        self.client = ComputeManagementClient(self.get_credentials(), self.subscription_id)

    def get_credentials(self) -> TokenCredential:
        # to ensure your tables & blobs have the right permissions see
        # https://learn.microsoft.com/en-us/rest/api/storageservices/authorize-with-azure-active-directory
        if self.managed_identity:
            # this code path is what we use on our Azure VMs.
            return ManagedIdentityCredential(client_id=self.managed_identity)
        else:
            # this code path will only work on dev box with `az login` on a user account that has
            # been granted explicit "Key Vault Reader" and "Key Vault Secrets User" permissions on the keyvault.
            # Note: DefaultAzureCredential doesn't work on GCR sandbox machines, but AzureCliCredential does
            # work in both GCR sandbox and local dev box.
            return AzureCliCredential()

    def get_power_state(self, instance_view) -> str:
        """Find the power state status in the instance view of the VM."""
        for status in instance_view.statuses:
            if status.code and status.code.startswith("PowerState"):
                return status.code
        return "unknown"

    def get_vm_state(self, vm_name: str) -> AzureVmState:
        vm = self.client.virtual_machines.get(self.resource_group, vm_name)
        name = vm.name
        # https://learn.microsoft.com/en-us/python/api/azure-mgmt-compute/azure.mgmt.compute.v2015_06_15.models.instanceviewstatus?view=azure-python
        instance_view = self.client.virtual_machines.instance_view(self.resource_group, name)
        power_status = self.get_power_state(instance_view)
        is_windows = hasattr(vm.os_profile, "windows_configuration") and vm.os_profile.windows_configuration is not None
        is_linux = hasattr(vm.os_profile, "linux_configuration") and vm.os_profile.linux_configuration is not None
        result = AzureVmState(name, power_status, is_linux, is_windows)
        result.identity = vm.identity
        result.subscription_id = self.subscription_id
        result.resource_group = self.resource_group
        return result

    def list_vms(self) -> Dict[str, AzureVmState]:
        """Return current list of VM's including their power states, and os type (windows or linux)."""
        # https://learn.microsoft.com/en-us/python/api/azure-mgmt-compute/azure.mgmt.compute.v2015_06_15.models.virtualmachine?view=azure-python
        vms = self.client.virtual_machines.list(self.resource_group)
        results: Dict[str, AzureVmState] = {}
        for vm in vms:
            name = vm.name
            results[name] = self.get_vm_state(name)
        return results

    def try_start_vm(self, vm: AzureVmState):
        """Attempt to start the VM."""
        if self.unavailable_state:
            if time.time() > self.unavailable_retry_time:
                self.unavailable_state = False
                self.unavailable_retry_time = 0
            else:
                log.warning(f"Ignoring request to start vm {vm.name} because of capacity restrictions...")
                return
        try:
            log.info(f"VM {vm.name} is deallocated, attempting restart...")
            self.client.virtual_machines.begin_start(self.resource_group, vm.name)
            vm.on_start()
        except ResourceExistsError:
            err_status_msg = get_exception_info()
            log.error(f"VM {vm.name} failed to start: {err_status_msg}")
            # stop requesting VMs for a while so we don't get too many of these errors from Azure.
            self.unavailable_state = True
            self.unavailable_retry_time = time.time() + self.unavailable_timeout
            vm.starting = False
        except Exception as ex:
            s = str(ex)
            vm.starting = False
            if AZURE_VM_RUNNING_MSG in s:
                # ignore bogus warnings about "did not finish in the allotted time"...
                log.warning(s)
            else:
                s = get_exception_info()
                log.error(f"VM {vm.name} failed to start: {s}")
