# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import datetime
import logging
import os
import re
import subprocess

try:
    import magic
    HAVE_MAGIC = True
except ImportError:
    HAVE_MAGIC = False

try:
    import pefile
    import peutils
    HAVE_PEFILE = True
except ImportError:
    HAVE_PEFILE = False

from lib.cuckoo.common.abstracts import Processing
from lib.cuckoo.common.constants import CUCKOO_ROOT
from lib.cuckoo.common.objects import File
from lib.cuckoo.common.utils import convert_to_printable

log = logging.getLogger(__name__)

# Partially taken from
# http://malwarecookbook.googlecode.com/svn/trunk/3/8/pescanner.py

class PortableExecutable(object):
    """PE analysis."""

    def __init__(self, file_path):
        """@param file_path: file path."""
        self.file_path = file_path
        self.elf = None

    def _get_filetype(self, data):
        """Gets filetype, uses libmagic if available.
        @param data: data to be analyzed.
        @return: file type or None.
        """
        if not HAVE_MAGIC:
            return None

        try:
            ms = magic.open(magic.MAGIC_NONE)
            ms.load()
            file_type = ms.buffer(data)
        except:
            try:
                file_type = magic.from_buffer(data)
            except Exception:
                return None
        finally:
            try:
                ms.close()
            except:
                pass

        return file_type

    def _get_peid_signatures(self):
        """Gets PEID signatures.
        @return: matched signatures or None.
        """
        if not self.pe:
            return None

        try:
            sig_path = os.path.join(CUCKOO_ROOT, "data",
                                    "peutils", "UserDB.TXT")
            signatures = peutils.SignatureDatabase(sig_path)
            return signatures.match(self.pe, ep_only=True)
        except:
            return None

    def _get_imported_symbols(self):
        """Gets imported symbols.
        @return: imported symbols dict or None.
        """
        if not self.pe:
            return None

        imports = []

        if hasattr(self.pe, "DIRECTORY_ENTRY_IMPORT"):
            for entry in self.pe.DIRECTORY_ENTRY_IMPORT:
                try:
                    symbols = []
                    for imported_symbol in entry.imports:
                        symbol = {}
                        symbol["address"] = hex(imported_symbol.address)
                        symbol["name"] = imported_symbol.name
                        symbols.append(symbol)

                    imports_section = {}
                    imports_section["dll"] = convert_to_printable(entry.dll)
                    imports_section["imports"] = symbols
                    imports.append(imports_section)
                except:
                    continue

        return imports

    def _get_exported_symbols(self):
        """Gets exported symbols.
        @return: exported symbols dict or None.
        """
        if not self.pe:
            return None

        exports = []

        if hasattr(self.pe, "DIRECTORY_ENTRY_EXPORT"):
            for exported_symbol in self.pe.DIRECTORY_ENTRY_EXPORT.symbols:
                symbol = {}
                symbol["address"] = hex(self.pe.OPTIONAL_HEADER.ImageBase +
                                        exported_symbol.address)
                symbol["name"] = exported_symbol.name
                symbol["ordinal"] = exported_symbol.ordinal
                exports.append(symbol)

        return exports

    def _get_sections(self):
        """Gets sections.
        @return: sections dict or None.
        """
        if not self.pe:
            return None

        sections = []

        for entry in self.pe.sections:
            try:
                section = {}
                section["name"] = convert_to_printable(entry.Name.strip("\x00"))
                section["virtual_address"] = "0x{0:08x}".format(entry.VirtualAddress)
                section["virtual_size"] = "0x{0:08x}".format(entry.Misc_VirtualSize)
                section["size_of_data"] = "0x{0:08x}".format(entry.SizeOfRawData)
                section["entropy"] = entry.get_entropy()
                sections.append(section)
            except:
                continue

        return sections

    def _get_resources(self):
        """Get resources.
        @return: resources dict or None.
        """
        if not self.pe:
            return None

        resources = []

        if hasattr(self.pe, "DIRECTORY_ENTRY_RESOURCE"):
            for resource_type in self.pe.DIRECTORY_ENTRY_RESOURCE.entries:
                try:
                    resource = {}

                    if resource_type.name is not None:
                        name = str(resource_type.name)
                    else:
                        name = str(pefile.RESOURCE_TYPE.get(resource_type.struct.Id))

                    if hasattr(resource_type, "directory"):
                        for resource_id in resource_type.directory.entries:
                            if hasattr(resource_id, "directory"):
                                for resource_lang in resource_id.directory.entries:
                                    data = self.pe.get_data(resource_lang.data.struct.OffsetToData, resource_lang.data.struct.Size)
                                    filetype = self._get_filetype(data)
                                    language = pefile.LANG.get(resource_lang.data.lang, None)
                                    sublanguage = pefile.get_sublang_name_for_lang(resource_lang.data.lang, resource_lang.data.sublang)

                                    resource["name"] = name
                                    resource["offset"] = "0x{0:08x}".format(resource_lang.data.struct.OffsetToData)
                                    resource["size"] = "0x{0:08x}".format(resource_lang.data.struct.Size)
                                    resource["filetype"] = filetype
                                    resource["language"] = language
                                    resource["sublanguage"] = sublanguage
                                    resources.append(resource)
                except:
                    continue

        return resources

    def _get_versioninfo(self):
        """Get version info.
        @return: info dict or None.
        """
        if not self.pe:
            return None

        infos = []
        if hasattr(self.pe, "VS_VERSIONINFO"):
            if hasattr(self.pe, "FileInfo"):
                for entry in self.pe.FileInfo:
                    try:
                        if hasattr(entry, "StringTable"):
                            for st_entry in entry.StringTable:
                                for str_entry in st_entry.entries.items():
                                    entry = {}
                                    entry["name"] = convert_to_printable(str_entry[0])
                                    entry["value"] = convert_to_printable(str_entry[1])
                                    infos.append(entry)
                        elif hasattr(entry, "Var"):
                            for var_entry in entry.Var:
                                if hasattr(var_entry, "entry"):
                                    entry = {}
                                    entry["name"] = convert_to_printable(var_entry.entry.keys()[0])
                                    entry["value"] = convert_to_printable(var_entry.entry.values()[0])
                                    infos.append(entry)
                    except:
                        continue

        return infos

    def _get_imphash(self):
        """Gets imphash.
        @return: imphash string or None.
        """
        if not self.pe:
            return None

        try:
            return self.pe.get_imphash()
        except AttributeError:
            return None

    def _get_timestamp(self):
        """Get compilation timestamp.
        @return: timestamp or None.
        """
        if not self.pe:
            return None

        try:
            pe_timestamp = self.pe.FILE_HEADER.TimeDateStamp
        except AttributeError:
            return None

        dt = datetime.datetime.fromtimestamp(pe_timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _get_pdb_path(self):
        """Get the path to any available debugging symbols."""
        try:
            for entry in getattr(self.pe, "DIRECTORY_ENTRY_DEBUG", []):
                raw_offset = entry.struct.PointerToRawData
                size_data = entry.struct.SizeOfData
                debug_data = self.pe.__data__[raw_offset:raw_offset+size_data]

                if debug_data.startswith("RSDS"):
                    return debug_data[24:].strip("\x00")
        except:
            log.exception("Exception parsing PDB path")

    def run(self):
        """Run analysis.
        @return: analysis results dict or None.
        """
        if not os.path.exists(self.file_path):
            return None

        try:
            self.pe = pefile.PE(self.file_path)
        except pefile.PEFormatError:
            return None

        results = {}
        results["peid_signatures"] = self._get_peid_signatures()
        results["pe_imports"] = self._get_imported_symbols()
        results["pe_exports"] = self._get_exported_symbols()
        results["pe_sections"] = self._get_sections()
        results["pe_resources"] = self._get_resources()
        results["pe_versioninfo"] = self._get_versioninfo()
        results["pe_imphash"] = self._get_imphash()
        results["pe_timestamp"] = self._get_timestamp()
        results["pdb_path"] = self._get_pdb_path()
        results["imported_dll_count"] = len([x for x in results["pe_imports"] if x.get("dll")])
        return results

class ELF:
    """ ELF analysis """
    
    def __init__(self, file_path):
        """@param file_path: file path."""
        self.file_path = file_path
    
    def __get_relocations(self):
        """Gets relocations.
        @return: relocations dict or None.
        """
        relocs = []
        
        process = subprocess.Popen(["/usr/bin/objdump",self.file_path, "-R"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # take output
        dump_result = process.communicate()[0]
        # format output
        dump_result = re.split("\n[ ]{0,}", dump_result)
        
        for i in range(0,len(dump_result)):
            if re.search("00", dump_result[i]):
                relocs.append(filter(None, re.split("\s", dump_result[i])))
        
        return relocs
    
    def _get_symbols(self):
        """Gets symbols.
        @return: symbols dict or None.
        """
        
        libs = []
        entry = []
        
        # dump dynamic symbols using 'objdump -T'
        process = subprocess.Popen(["/usr/bin/objdump",self.file_path, "-T"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        elf = process.communicate()[0]
        
        # Format to lines by splitting at '\n'
        elf = re.split("\n[ ]{0,}", elf)
            
        for i in range(0,len(elf)):
            if re.search("DF \*UND\*", elf[i]):
                entry.append(filter(None, re.split("\s", elf[i])))
        
        # extract library names
        lib_names = set()
        for e in entry:
            # check for existing library name
            if len(e) > 5:
                # add library to set
                lib_names.add(e[4])
        lib_names.add("None")
        
        # fetch relocation addresses
        relocs = self.__get_relocations()
        
        # find all symbols for each lib
        for lib in lib_names:
            symbols = []
            for e in entry:
                if lib == e[4]:
                    symbol = {}
                    symbol["address"] = "0x{0}".format(e[0])
                    symbol["name"] = e[5]
                    
                    # fetch the address from relocation sections if possible
                    for r in relocs:
                        if symbol["name"] in r:
                            symbol["address"] = "0x{0}".format(r[0])
                    symbols.append(symbol)
                
            if symbols:
                symbol_section = {}
                symbol_section["lib"] = lib
                symbol_section["symbols"] = symbols
                libs.append(symbol_section)
                
        return libs
            
    def _get_sections(self):
        """Gets sections.
        @return: sections dict or None.
        """

        sections = []
        entry = []
        
        process = subprocess.Popen(["/usr/bin/readelf", self.file_path, "-S", "-W"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        elf = process.communicate()[0]
        
        # Format to lines by splitting at '\n'
        tmp = re.split("\n[ ]{0,}", elf)
        for i in range(0,len(tmp)):
            # Filter lines containing [xx]
            if re.search("^\[[ 0-9][1-9]\]", tmp[i]):
                # Regex: Split all whitespaces '\s' if they are not proceeded '(?<!\[)' by a '['
                # remove all splitted whitespaces from the list filter()'
                entry.append(filter(None, re.split("(?<!\[)\s", tmp[i])))
                
        for e in entry:
            try:
                section = {}
                section["name"] = e[1]
                section["type"] = e[2]
                section["virtual_address"] = "0x{0}".format(e[3])
                section["virtual_size"] = "0x{0}".format(e[4])
                sections.append(section)
                
            except:
                continue
            
        return sections
        
    def run(self):
        """Run analysis.
        @return: analysis results dict or None.
        """
        if not os.path.exists(self.file_path):
            return None
        
        results = {}
        results["elf_sections"] = self._get_sections()
        results["elf_symbols"] = self._get_symbols()
        return results
        
class Static(Processing):
    """Static analysis."""
    PUBKEY_RE = "(-----BEGIN PUBLIC KEY-----[a-zA-Z0-9\\n\\+/]+-----END PUBLIC KEY-----)"
    PRIVKEY_RE = "(-----BEGIN RSA PRIVATE KEY-----[a-zA-Z0-9\\n\\+/]+-----END RSA PRIVATE KEY-----)"

    def run(self):
        """Run analysis.
        @return: results dict.
        """
        self.key = "static"
        static = {}

        if self.task["category"] == "file":
            if "PE32" in File(self.file_path).get_type():
                if HAVE_PEFILE:
                    static.update(PortableExecutable(self.file_path).run())

                    static["keys"] = self._get_keys()
                    
            if "ELF" in File(self.file_path).get_type():
                static.update(ELF(self.file_path).run())

        return static

    def _get_keys(self):
        """Get any embedded plaintext public and/or private keys."""
        buf = open(self.file_path).read()
        ret = []

        ret += re.findall(self.PUBKEY_RE, buf)
        ret += re.findall(self.PRIVKEY_RE, buf)
        return ret
