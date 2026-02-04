import argparse
import json
import time
from typing import Dict, List, Tuple

from az_cmd import run_az_cmd
from azure_vms import AzureVms, AzureVmState


def list_extensions(vm: AzureVmState, resource_group: str) -> Dict[str, dict]:
    cmd = f"vm extension list --resource-group {resource_group} --vm-name {vm.name}"
    found: Dict[str, dict] = {}
    data = run_az_cmd(cmd, f"listing extensions on {vm.name}")
    for ext in data:
        name = ext["name"]
        found[name] = ext
    return found


def get_monitor_agent_name(vm: AzureVmState) -> str:
    if vm.is_linux:
        return "AzureMonitorLinuxAgent"
    else:
        return "AzureMonitorWindowsAgent"


def check_monitor_agent(vm: AzureVmState, extensions: Dict[str, dict]) -> bool:
    name = get_monitor_agent_name(vm)
    if name in extensions:
        ext = extensions[name]
        state = ext["provisioningState"]
        print(f"Monitor agent {name} is installed and in state: {state}")
        return True
    return False


def install_monitor_agent(vm: AzureVmState, subscription: str, resource_group: str, uami: str):
    name = get_monitor_agent_name(vm)
    vm_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}/providers/Microsoft.Compute/"
        + f"virtualMachines/{vm.name}"
    )

    cmd = (
        f"vm extension set --name {name} --publisher Microsoft.Azure.Monitor "
        + f"--ids {vm_id} --enable-auto-upgrade true --no-wait"
    )
    if vm.is_windows and uami:
        managed_identity = (
            f"/subscriptions/{subscription}/resourceGroups/{resource_group}/providers/"
            + f"Microsoft.ManagedIdentity/userAssignedIdentities/{uami}"
        )
        settings = {
            "authentication": {
                "managedIdentity": {"identifier-name": "mi_res_id", "identifier-value": managed_identity}
            }
        }
        data = json.dumps(settings).replace(" ", "")
        cmd += f" --settings '{data}'"

    run_az_cmd(cmd, f"Installing monitor agent {name} on {vm.name}", no_data_ok=True)


def get_guest_configuration_extension_name(vm: AzureVmState) -> Tuple[str, str]:
    if vm.is_windows:
        name = "ConfigurationforWindows"
        extension_name = "AzurePolicyforWindows"
    else:
        name = "ConfigurationForLinux"
        extension_name = "AzurePolicyforLinux"
    return name, extension_name


def check_guest_configuration_extension(vm: AzureVmState, extensions: Dict[str, dict]) -> bool:
    _, extension_name = get_guest_configuration_extension_name(vm)
    if extension_name in extensions:
        ext = extensions[extension_name]
        state = ext["provisioningState"]
        print(f"Guest configuration extension {extension_name} is installed and in state: {state}")
        return True
    return False


def install_guest_configuration_extension(vm: AzureVmState, resource_group: str):
    name, extension_name = get_guest_configuration_extension_name(vm)
    cmd = (
        f"vm extension set --publisher Microsoft.GuestConfiguration --name {name} "
        + f"--extension-instance-name {extension_name} --resource-group {resource_group} "
        + f"--vm-name {vm.name} --enable-auto-upgrade true --no-wait"
    )

    run_az_cmd(cmd, f"Installing guest extensions on {vm.name}", no_data_ok=True)


def get_aad_ssh_extension_name(vm: AzureVmState) -> str:
    if vm.is_windows:
        return "AADLoginForWindows"
    else:
        return "AADSSHLoginForLinux"


def check_aad_ssh_login(vm: AzureVmState, extensions: Dict[str, dict]) -> bool:
    name = get_aad_ssh_extension_name(vm)
    if name in extensions:
        ext = extensions[name]
        state = ext["provisioningState"]
        print(f"AAD SSH Login extension {name} is installed and in state: {state}")
        return True
    return False


def install_aad_ssh_login(vm: AzureVmState, resource_group: str):
    name = get_aad_ssh_extension_name(vm)
    cmd = (
        f"vm extension set --publisher Microsoft.Azure.ActiveDirectory --name {name} "
        + f"--resource-group {resource_group} "
        + f"--vm-name {vm.name} "
        + "--no-wait"
    )

    run_az_cmd(cmd, f"Installing aad ssh extension on {vm.name}", no_data_ok=True)


def get_guest_attestation_extension_publisher(vm: AzureVmState) -> str:
    if vm.is_windows:
        return "Microsoft.Azure.Security.WindowsAttestation"
    else:
        return "Microsoft.Azure.Security.LinuxAttestation"


def check_guest_attestation(vm: AzureVmState, extensions: Dict[str, dict]) -> bool:
    name = "GuestAttestation"
    if name in extensions:
        ext = extensions[name]
        state = ext["provisioningState"]
        print(f"GuestAttestation extension {name} is installed and in state: {state}")
        return True
    return False


def install_guest_attestation(vm: AzureVmState, resource_group: str):
    publisher = get_guest_attestation_extension_publisher(vm)
    cmd = (
        f"vm extension set --publisher {publisher} --name GuestAttestation "
        + f"--resource-group {resource_group} "
        + f"--vm-name {vm.name} "
        + "--no-wait"
    )

    run_az_cmd(cmd, f"Installing GuestAttestation extension on {vm.name}", no_data_ok=True)


class Timeout:
    def __init__(self, timeout_seconds: int, interval: int, title: str):
        self.title = title
        self.interval = interval
        self.timeout_seconds = timeout_seconds
        self.start = time.time()

    def step(self):
        if time.time() - self.start < self.timeout_seconds:
            time.sleep(self.interval)
            return True
        else:
            print(f"Timeout: {self.title}")
            return False


def process_vm(monitor: AzureVms, vm: AzureVmState, subscription_id: str, resource_group: str, uami: str):
    print(f"Checking extensions are installed on VM {vm.name}...")
    if not vm.is_running():
        print(f"Wait for {vm.name} to be running...")
        monitor.try_start_vm(vm)
        timeout = Timeout(600, 15, f"starting vm {vm.name}")
        while not vm.is_running() and timeout.step():
            vm = monitor.get_vm_state(vm.name)

    extensions = list_extensions(vm, resource_group)
    if not check_monitor_agent(vm, extensions):
        install_monitor_agent(vm, subscription_id, resource_group, uami)

    if not check_guest_configuration_extension(vm, extensions):
        install_guest_configuration_extension(vm, resource_group)

    if not check_aad_ssh_login(vm, extensions):
        install_aad_ssh_login(vm, resource_group)

    if not check_guest_attestation(vm, extensions):
        install_guest_attestation(vm, resource_group)


def parse_command_line():
    """Parse command line arguments that specify optional vm name"""
    parser = argparse.ArgumentParser(description="Install extensions on VMs")
    parser.add_argument(
        "vms", type=str, nargs="+", help="One or more VM names to install extensions on, default is all of them"
    )
    # add resource-group argument
    parser.add_argument(
        "--resource_group",
        "-g",
        type=str,
        required=True,
        help="Resource group where the VMs are located",
    )
    # add subscription-id argument
    parser.add_argument(
        "--subscription",
        "-s",
        type=str,
        required=True,
        help="Subscription ID where the VMs are located",
    )
    # add --uami argument
    parser.add_argument(
        "--uami",
        type=str,
        default="",
        help="Optional User Assigned Managed Identity name to use for the Monitor Agent on Windows VMs",
    )
    args = parser.parse_args()
    return args


def install_extensions(names: List[str], resource_group: str, subscription_id: str, uami: str):
    print("finding vms...")
    monitor = AzureVms(subscription_id, resource_group)
    vms = monitor.list_vms()

    found: Dict[str, AzureVmState] = {}
    if names:
        for vm_name in names:
            if vm_name not in vms:
                print(f"VM {vm_name} not found")
            else:
                found[vm_name] = vms[vm_name]
        vms = found

    for vm in vms.values():
        process_vm(monitor, vm, subscription_id, resource_group, uami)


def main():
    args = parse_command_line()
    install_extensions(args.vms, args.resource_group, args.subscription, args.uami)


if __name__ == "__main__":
    main()
