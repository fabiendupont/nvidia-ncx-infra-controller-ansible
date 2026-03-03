# SPDX-FileCopyrightText: Copyright (c) 2026 Fabien Dupont
# SPDX-License-Identifier: Apache-2.0

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
---
name: nvidia.bare_metal.bmm
short_description: Dynamic inventory from NVIDIA Bare Metal Manager
description:
    - Queries the NVIDIA Bare Metal Manager API for instances and builds
      an Ansible inventory from the results.
    - Instances become hosts. IP addresses are extracted from instance
      interfaces. Labels, site, VPC, and instance type are mapped to
      host variables and groups.
    - Reuses the same HTTP client and auth as the nvidia.bare_metal modules.
version_added: "1.0.0"
author: NVIDIA Bare Metal Manager Dev Team

options:
    plugin:
        description: Must be C(nvidia.bare_metal.bmm).
        required: true
        choices: ['nvidia.bare_metal.bmm']
    api_url:
        description: URL of the NVIDIA Bare Metal Manager API.
        type: str
        required: true
        env:
            - name: NVIDIA_BMM_API_URL
    api_token:
        description: JWT bearer token for API authentication.
        type: str
        required: true
        env:
            - name: NVIDIA_BMM_API_TOKEN
    org:
        description: Organization name for API requests.
        type: str
        required: true
        env:
            - name: NVIDIA_BMM_ORG
    api_path_prefix:
        description:
            - API path prefix. Use C(carbide) for direct access, C(forge) for the NVIDIA proxy.
        type: str
        default: carbide
        env:
            - name: NVIDIA_BMM_API_PATH_PREFIX
    filters:
        description:
            - Filter instances by API query parameters.
            - Keys are snake_case parameter names (e.g., C(site_id), C(vpc_id), C(status)).
        type: dict
        default: {}
    ansible_host_source:
        description:
            - Where to get the C(ansible_host) value for each instance.
            - C(first_interface_ip) uses the first IP address from the first interface.
            - C(name) uses the instance name (useful with DNS).
        type: str
        default: first_interface_ip
        choices: ['first_interface_ip', 'name']
    groups_from:
        description:
            - List of instance fields to auto-create groups from.
            - For each field, a group is created per unique value.
            - For example, C(site_id) creates one group per site.
        type: list
        elements: str
        default: ['site_id', 'vpc_id', 'status']
    group_by_labels:
        description:
            - Create groups from instance label key-value pairs.
            - Group names are formed as C(label_KEY_VALUE).
        type: bool
        default: true
    group_prefix:
        description:
            - Prefix for auto-created group names.
        type: str
        default: bmm_
    compose:
        description:
            - Jinja2 expressions to create host variables.
            - See Ansible constructed inventory documentation.
        type: dict
        default: {}
    groups:
        description:
            - Jinja2 conditionals to assign hosts to groups.
            - See Ansible constructed inventory documentation.
        type: dict
        default: {}
    keyed_groups:
        description:
            - Create groups based on variable values with a key prefix.
            - See Ansible constructed inventory documentation.
        type: list
        elements: dict
        default: []
    strict:
        description:
            - If true, raise errors on Jinja2 template failures in compose/groups.
        type: bool
        default: false

extends_documentation_fragment:
    - constructed
'''

EXAMPLES = r'''
---
# File: inventory/bmm.yml
# Discovers all Ready instances at a specific site.

plugin: nvidia.bare_metal.bmm
api_url: https://bmm-api.example.com
api_path_prefix: forge
org: my-org

filters:
  site_id: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
  status: Ready

# Auto-create groups from these fields
groups_from:
  - vpc_id
  - status

# Also create groups from labels (e.g., label_cluster_nvl72)
group_by_labels: true

# Set ansible_user for all discovered hosts
compose:
  ansible_user: "'root'"

# Create a "gpu_nodes" group from a label
groups:
  gpu_nodes: labels.get('role') == 'compute'
'''

import os

from ansible.plugins.inventory import BaseInventoryPlugin, Constructable
from ansible.errors import AnsibleParserError

from ansible_collections.nvidia.bare_metal.plugins.module_utils.common import (
    camel_to_snake,
    snake_to_camel,
    convert_keys,
)


class _InventoryModule(object):
    """Thin adapter that looks like an AnsibleModule to BareMetalClient."""

    def __init__(self, api_url, api_token, org, api_path_prefix):
        self.params = {
            'api_url': api_url,
            'api_token': api_token,
            'org': org,
            'api_path_prefix': api_path_prefix,
        }

    def fail_json(self, msg, **kwargs):
        raise AnsibleParserError(msg)


class InventoryModule(BaseInventoryPlugin, Constructable):

    NAME = 'nvidia.bare_metal.bmm'

    def verify_file(self, path):
        """Accept files ending in .bmm.yml or .bmm.yaml."""
        if super(InventoryModule, self).verify_file(path):
            if path.endswith(('.bmm.yml', '.bmm.yaml')):
                return True
        return False

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path, cache)
        self._read_config_data(path)

        api_url = self.get_option('api_url')
        api_token = self.get_option('api_token')
        org = self.get_option('org')
        api_path_prefix = self.get_option('api_path_prefix')
        filters = self.get_option('filters')
        host_source = self.get_option('ansible_host_source')
        groups_from = self.get_option('groups_from')
        group_by_labels = self.get_option('group_by_labels')
        group_prefix = self.get_option('group_prefix')
        strict = self.get_option('strict')

        if not api_url or not api_token or not org:
            raise AnsibleParserError(
                'api_url, api_token, and org are required '
                '(set in the inventory file or via NVIDIA_BMM_* env vars)'
            )

        # Import the client here so the module_utils path is resolved at runtime
        from ansible_collections.nvidia.bare_metal.plugins.module_utils.client import BareMetalClient

        fake_module = _InventoryModule(api_url, api_token, org, api_path_prefix)
        client = BareMetalClient(fake_module)

        # Build query params from filters
        query_params = {}
        for k, v in (filters or {}).items():
            query_params[snake_to_camel(k)] = v

        # Fetch instances
        instance_path = '/v2/org/{org}/carbide/instance'
        instances = client.list_all(instance_path, params=query_params)

        # Create the catch-all group
        all_group = '%sall' % group_prefix
        self.inventory.add_group(all_group)

        for raw_instance in instances:
            instance = convert_keys(raw_instance, camel_to_snake)
            name = instance.get('name')
            if not name:
                name = instance.get('id', 'unknown')

            self.inventory.add_host(name, group=all_group)

            # Set ansible_host
            if host_source == 'first_interface_ip':
                ansible_host = self._extract_first_ip(instance)
                if ansible_host:
                    self.inventory.set_variable(name, 'ansible_host', ansible_host)
            elif host_source == 'name':
                self.inventory.set_variable(name, 'ansible_host', name)

            # Set all instance fields as host vars under a 'bmm' namespace
            self.inventory.set_variable(name, 'bmm', instance)

            # Also set commonly-needed fields as top-level vars
            for field in ('id', 'site_id', 'vpc_id', 'instance_type_id',
                          'machine_id', 'operating_system_id', 'status'):
                val = instance.get(field)
                if val is not None:
                    self.inventory.set_variable(name, 'bmm_%s' % field, val)

            # Set labels as top-level vars
            labels = instance.get('labels') or {}
            self.inventory.set_variable(name, 'bmm_labels', labels)

            # Auto-create groups from instance fields
            for field in (groups_from or []):
                val = instance.get(field)
                if val:
                    group_name = self._sanitize_group('%s%s_%s' % (
                        group_prefix, field, val,
                    ))
                    self.inventory.add_group(group_name)
                    self.inventory.add_host(name, group=group_name)

            # Auto-create groups from labels
            if group_by_labels and labels:
                for lk, lv in labels.items():
                    group_name = self._sanitize_group('%slabel_%s_%s' % (
                        group_prefix, lk, lv,
                    ))
                    self.inventory.add_group(group_name)
                    self.inventory.add_host(name, group=group_name)

            # Constructed features: compose, groups, keyed_groups
            self._set_composite_vars(
                self.get_option('compose'), instance, name, strict=strict,
            )
            self._add_host_to_composed_groups(
                self.get_option('groups'), instance, name, strict=strict,
            )
            self._add_host_to_keyed_groups(
                self.get_option('keyed_groups'), instance, name, strict=strict,
            )

    def _extract_first_ip(self, instance):
        """Extract the first IP address from the instance's interfaces."""
        for iface_field in ('interfaces', 'machine_interfaces'):
            interfaces = instance.get(iface_field) or []
            for iface in interfaces:
                ips = iface.get('ip_addresses') or []
                if ips:
                    return ips[0]
        return None

    def _sanitize_group(self, name):
        """Make a string safe for use as an Ansible group name."""
        return ''.join(c if c.isalnum() or c == '_' else '_' for c in name)
