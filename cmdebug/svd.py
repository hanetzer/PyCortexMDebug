#!/usr/bin/env python3
"""
This file is part of PyCortexMDebug

PyCortexMDebug is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

PyCortexMDebug is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with PyCortexMDebug.  If not, see <http://www.gnu.org/licenses/>.
"""

import lxml.objectify as objectify
import sys
from collections import OrderedDict
import os
import pickle
import traceback
import re
import warnings

from typing import Dict, Tuple, Any, Iterable, Union


class SmartDict:
    """
    Dictionary for search by case-insensitive lookup and/or prefix lookup
    """

    od: OrderedDict
    casemap: Dict[str, Any]

    def __init__(self) -> None:
        self.od = OrderedDict()
        self.casemap = {}

    def __getitem__(self, key: str) -> Any:
        if key in self.od:
            return self.od[key]

        if key.lower() in self.casemap:
            return self.od[self.casemap[key.lower()]]

        return self.od[self.prefix_match(key)]

    def is_ambiguous(self, key: str) -> bool:
        return key not in self.od and key not in self.casemap and len(list(self.prefix_match_iter(key))) > 1

    def prefix_match_iter(self, key: str) -> Any:
        name, number = re.match(r'^(.*?)([0-9]*)$', key.lower()).groups()
        for entry, od_key in self.casemap.items():
            if entry.startswith(name) and entry.endswith(number):
                yield od_key

    def prefix_match(self, key: str) -> Any:
        for od_key in self.prefix_match_iter(key):
            return od_key
        return None

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self.od:
            warnings.warn(f'Duplicate entry {key}')
        elif key.lower() in self.casemap:
            warnings.warn(f'Entry {key} differs from duplicate {self.casemap[key.lower()]} only in cAsE')

        self.casemap[key.lower()] = key
        self.od[key] = value

    def __delitem__(self, key: str) -> None:
        if self.casemap[key.lower()] == key:  # Check that we did not overwrite this entry
            del self.casemap[key.lower()]
        del self.od[key]

    def __contains__(self, key: str) -> bool:
        return key in self.od or key.lower() in self.casemap or self.prefix_match(key)

    def __iter__(self) -> Iterable[Any]:
        return iter(self.od)

    def __len__(self) -> int:
        return len(self.od)

    def items(self) -> Iterable[Tuple[str, Any]]:
        return self.od.items()

    def keys(self) -> Iterable[Any]:
        return self.od.keys()

    def values(self) -> Iterable[Any]:
        return self.od.values()

    def __str__(self) -> str:
        return str(self.od)


class SVDNonFatalError(Exception):
    """ Exception class for non-fatal errors
    So far, these have related to quirks in some vendor SVD files which are reasonable to ignore
    """

    def __init__(self, m: str) -> None:
        self.m = m
        self.exc_info = sys.exc_info()

    def __str__(self) -> str:
        s = "Non-fatal: {}".format(self.m)
        s += "\n" + str("".join(traceback.format_exc())).strip()
        return s


class SVDFile:
    """
    A parsed SVD file
    """

    peripherals: SmartDict
    base_address: int

    def __init__(self, fname: str) -> None:
        """

        Args:
            fname: Filename for the SVD file
        """
        f = objectify.parse(os.path.expanduser(fname))
        root = f.getroot()
        periph = root.peripherals.getchildren()
        self.peripherals = SmartDict()
        self.base_address = 0

        # XML elements
        for p in periph:
            try:
                if p.tag == "peripheral":
                    self.peripherals[str(p.name)] = SVDPeripheral(p, self)
                else:
                    # This is some other tag
                    pass
            except SVDNonFatalError as e:
                print(e)


def add_register(parent: Union["SVDPeripheral", "SVDRegisterCluster"], node):
    """
    Add a register node to a peripheral

    Args:
        parent: Parent SVDPeripheral object
        node: XML file node fot of the register
    """

    if hasattr(node, "dim"):
        dim = int(str(node.dim), 0)
        # dimension is not used, number of split indexes should be same
        incr = int(str(node.dimIncrement), 0)
        default_dim_index = ",".join((str(i) for i in range(dim)))
        dim_index = str(getattr(node, "dimIndex", default_dim_index))
        indices = dim_index.split(',')
        offset = 0
        for i in indices:
            name = str(node.name) % i
            reg = SVDPeripheralRegister(node, parent)
            reg.name = name
            reg.offset += offset
            parent.registers[name] = reg
            offset += incr
    else:
        try:
            reg = SVDPeripheralRegister(node, parent)
            name = str(node.name)
            if name not in parent.registers:
                parent.registers[name] = reg
            else:
                if hasattr(node, "alternateGroup"):
                    print(f"Register {name} has an alternate group")
        except SVDNonFatalError as e:
            print(e)


def add_cluster(parent: "SVDPeripheral", node) -> None:
    """
    Add a register cluster to a peripheral
    """
    if hasattr(node, "dim"):
        dim = int(str(node.dim), 0)
        # dimension is not used, number of split indices should be same
        incr = int(str(node.dimIncrement), 0)
        default_dim_index = ",".join((str(i) for i in range(dim)))
        dim_index = str(getattr(node, "dimIndex", default_dim_index))
        indices = dim_index.split(',')
        offset = 0
        for i in indices:
            name = str(node.name) % i
            cluster = SVDRegisterCluster(node, parent)
            cluster.name = name
            cluster.address_offset += offset
            cluster.base_address += offset
            parent.clusters[name] = cluster
            offset += incr
    else:
        try:
            parent.clusters[str(node.name)] = SVDRegisterCluster(node, parent)
        except SVDNonFatalError as e:
            print(e)


class SVDRegisterCluster:
    """
    Register cluster
    """

    parent_base_address: int
    parent_name: str
    address_offset: int
    base_address: int
    description: str
    name: str
    registers: SmartDict
    clusters: SmartDict

    def __init__(self, svd_elem, parent: "SVDPeripheral"):
        """

        Args:
            svd_elem: XML element for the register cluster
            parent: Parent SVDPeripheral object
        """
        self.parent_base_address = parent.base_address
        self.parent_name = parent.name
        self.address_offset = int(str(svd_elem.addressOffset), 0)
        self.base_address = self.address_offset + self.parent_base_address
        # This doesn't inherit registers from anything
        children = svd_elem.getchildren()
        self.description = str(getattr(svd_elem, "description", ""))
        self.name = str(svd_elem.name)
        self.registers = SmartDict()
        self.clusters = SmartDict()
        for r in children:
            if r.tag == "register":
                add_register(self, r)

    def refactor_parent(self, parent: "SVDPeripheral"):
        self.parent_base_address = parent.base_address
        self.parent_name = parent.name
        self.base_address = self.parent_base_address + self.address_offset
        values = self.registers.values()
        for r in values:
            r.refactor_parent(self)

    def __str__(self):
        return str(self.name)


class SVDPeripheral:
    """
    This is a peripheral as defined in the SVD file
    """
    parent_base_address: int
    name: str
    description: str

    def __init__(self, svd_elem, parent: SVDFile) -> None:
        """

        Args:
            svd_elem: XML element for the peripheral
            parent: Parent SVDFile object
        """
        self.parent_base_address = parent.base_address

        # Look for a base address, as it is required
        if not hasattr(svd_elem, "baseAddress"):
            raise SVDNonFatalError(f"Periph without base address")
        self.base_address = int(str(svd_elem.baseAddress), 0)
        if 'derivedFrom' in svd_elem.attrib:
            derived_from = svd_elem.attrib['derivedFrom']
            try:
                self.name = str(svd_elem.name)
            except AttributeError:
                self.name = parent.peripherals[derived_from].name
            try:
                self.description = str(svd_elem.description)
            except AttributeError:
                self.description = parent.peripherals[derived_from].description

            # pickle is faster than deepcopy by up to 50% on svd files with a
            # lot of derivedFrom definitions
            def copier(a: Any) -> Any:
                return pickle.loads(pickle.dumps(a))

            self.registers = copier(parent.peripherals[derived_from].registers)
            self.clusters = copier(parent.peripherals[derived_from].clusters)
            self.refactor_parent(parent)
        else:
            # This doesn't inherit registers from anything
            self.description = str(getattr(svd_elem, "description", ""))
            self.name = str(svd_elem.name)
            self.registers = SmartDict()
            self.clusters = SmartDict()

            if hasattr(svd_elem, "registers"):
                registers = [r for r in svd_elem.registers.getchildren() if r.tag in ["cluster", "register"]]
                for r in registers:
                    if r.tag == "cluster":
                        add_cluster(self, r)
                    elif r.tag == "register":
                        add_register(self, r)

    def refactor_parent(self, parent: SVDFile) -> None:
        self.parent_base_address = parent.base_address
        values = self.registers.values()
        for r in values:
            r.refactor_parent(self)

        for c in self.clusters.values():
            c.refactor_parent(self)

    def __str__(self) -> str:
        return str(self.name)


class SVDPeripheralRegister:
    """
    A register within a peripheral
    """

    parent_base_address: int
    name: str
    description: str
    offset: int
    access: str
    size: int
    fields: SmartDict

    def __init__(self, svd_elem, parent: SVDPeripheral) -> None:
        self.parent_base_address = parent.base_address
        self.offset = int(str(svd_elem.addressOffset), 0)
        if 'derivedFrom' in svd_elem.attrib:
            derived_from = svd_elem.attrib['derivedFrom']
            try:
                self.name = str(svd_elem.name)
            except AttributeError:
                self.name = parent.registers[derived_from].name
            try:
                self.description = str(svd_elem.description)
            except AttributeError:
                self.description = str(getattr(svd_elem, "description", ""))
            try:
                self.access = str(svd_elem.access)
            except AttributeError:
                self.access = str(getattr(svd_elem, "access", "read-write"))
            try:
                self.size = str(svd_elem.size)
            except AttributeError:
                self.size = getattr(svd_elem, "size", 0x20)

            def copier(a: Any) -> Any:
                return pickle.loads(pickle.dumps(a))

            self.fields = copier(parent.registers[derived_from].fields)
            self.refactor_parent(parent)
        else:
            self.description = str(getattr(svd_elem, "description", ""))
            self.name = str(svd_elem.name)
            self.access = str(getattr(svd_elem, "access", "read-write"))
            self.size = getattr(svd_elem, "size", 0x20)

            self.fields = SmartDict()
            if hasattr(svd_elem, "fields"):
                # Filter fields to only consider those of tag "field"
                fields = [f for f in svd_elem.fields.getchildren() if f.tag == "field"]
                for f in fields:
                    self.fields[str(f.name)] = SVDPeripheralRegisterField(f, self)

    def refactor_parent(self, parent: SVDPeripheral) -> None:
        self.parent_base_address = parent.base_address

    def address(self) -> int:
        return self.parent_base_address + self.offset

    def readable(self) -> bool:
        return self.access in ["read-only", "read-write", "read-writeOnce"]

    def writable(self) -> bool:
        return self.access in ["write-only", "read-write", "writeOnce", "read-writeOnce"]

    def __str__(self) -> str:
        return str(self.name)


class SVDPeripheralRegisterField:
    """
    Field within a register
    """

    name: str
    description: str
    offset: int
    width: int
    access: str
    enum: Dict[int, Tuple[str, str]]

    def __init__(self, svd_elem, parent: SVDPeripheralRegister) -> None:
        self.name = str(svd_elem.name)
        self.description = str(getattr(svd_elem, "description", ""))

        # Try to extract a bit range (offset and width) from the available fields
        if hasattr(svd_elem, "bitOffset") and hasattr(svd_elem, "bitWidth"):
            self.offset = int(str(svd_elem.bitOffset))
            self.width = int(str(svd_elem.bitWidth))
        elif hasattr(svd_elem, "bitRange"):
            bitrange = list(map(int, str(svd_elem.bitRange).strip()[1:-1].split(":")))
            self.offset = bitrange[1]
            self.width = 1 + bitrange[0] - bitrange[1]
        else:
            assert hasattr(svd_elem, "lsb") and hasattr(svd_elem, "msb"),\
                f"Range not found for field {self.name} in register {parent}"
            lsb = int(str(svd_elem.lsb))
            msb = int(str(svd_elem.msb))
            self.offset = lsb
            self.width = 1 + msb - lsb

        self.access = str(getattr(svd_elem, "access", parent.access))
        self.enum = {}

        if hasattr(svd_elem, "enumeratedValues"):
            values = [v for v in svd_elem.enumeratedValues.getchildren() if v.tag == "enumeratedValue"]
            for v in values:
                # Skip the "name" tag and any entries that don't have a value
                if v.tag == "name" or not hasattr(v, "value"):
                    continue
                # Some Kinetis parts have values with # instead of 0x...
                value = str(v.value).replace("#", "0x")
                description = str(getattr(v, "description", ""))
                try:
                    index = int(value, 0)
                    self.enum[int(value, 0)] = (str(v.name), description)
                except ValueError:
                    # If the value couldn't be converted as a single integer, skip it
                    pass

    def readable(self) -> bool:
        return self.access in ["read-only", "read-write", "read-writeOnce"]

    def writable(self) -> bool:
        return self.access in ["write-only", "read-write", "writeOnce", "read-writeOnce"]

    def __str__(self) -> str:
        return str(self.name)


def _main() -> None:
    """
    Basic test to parse a file and do some things
    """

    for f in sys.argv[1:]:
        print("Testing file: {}".format(f))
        svd = SVDFile(f)
        print(svd.peripherals)
        key = list(svd.peripherals)[0]
        print("Registers in peripheral '{}':".format(key))
        print(svd.peripherals[key].registers)
        print("Done testing file: {}".format(f))


if __name__ == '__main__':
    _main()
