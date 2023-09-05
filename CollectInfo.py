import os
from datetime import datetime
from openpyxl import Workbook
import ctypes as ctypes
from ctypes import wintypes as wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)

ERROR_INVALID_FUNCTION  = 0x0001
ERROR_FILE_NOT_FOUND    = 0x0002
ERROR_PATH_NOT_FOUND    = 0x0003
ERROR_ACCESS_DENIED     = 0x0005
ERROR_SHARING_VIOLATION = 0x0020

SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
GROUP_SECURITY_INFORMATION = 0x00000002
DACL_SECURITY_INFORMATION  = 0x00000004
SACL_SECURITY_INFORMATION  = 0x00000008
LABEL_SECURITY_INFORMATION = 0x00000010

_DEFAULT_SECURITY_INFORMATION = (OWNER_SECURITY_INFORMATION |
    GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION |
    LABEL_SECURITY_INFORMATION)

LPDWORD = ctypes.POINTER(wintypes.DWORD)
SE_OBJECT_TYPE = wintypes.DWORD
SECURITY_INFORMATION = wintypes.DWORD

class SID_NAME_USE(wintypes.DWORD):
    _sid_types = dict(enumerate('''
        User Group Domain Alias WellKnownGroup DeletedAccount
        Invalid Unknown Computer Label'''.split(), 1))

    def __init__(self, value=None):
        if value is not None:
            if value not in self.sid_types:
                raise ValueError('invalid SID type')
            wintypes.DWORD.__init__(value)

    def __str__(self):
        if self.value not in self._sid_types:
            raise ValueError('invalid SID type')
        return self._sid_types[self.value]

    def __repr__(self):
        return 'SID_NAME_USE(%s)' % self.value

PSID_NAME_USE = ctypes.POINTER(SID_NAME_USE)

class PLOCAL(wintypes.LPVOID):
    _needs_free = False
    def __init__(self, value=None, needs_free=False):
        super(PLOCAL, self).__init__(value)
        self._needs_free = needs_free

    def __del__(self):
        if self and self._needs_free:
            kernel32.LocalFree(self)
            self._needs_free = False

PACL = PLOCAL

class PSID(PLOCAL):
    def __init__(self, value=None, needs_free=False):
        super(PSID, self).__init__(value, needs_free)

    def __str__(self):
        if not self:
            raise ValueError('NULL pointer access')
        sid = wintypes.LPWSTR()
        advapi32.ConvertSidToStringSidW(self, ctypes.byref(sid))
        try:
            return sid.value
        finally:
            if sid:
                kernel32.LocalFree(sid)

class PSECURITY_DESCRIPTOR(PLOCAL):
    def __init__(self, value=None, needs_free=False):
        super(PSECURITY_DESCRIPTOR, self).__init__(value, needs_free)
        self.pOwner = PSID()
        self.pGroup = PSID()
        self.pDacl = PACL()
        self.pSacl = PACL()
        # back references to keep this object alive
        self.pOwner._SD = self
        self.pGroup._SD = self
        self.pDacl._SD = self
        self.pSacl._SD = self

    def get_owner(self, system_name=None):
        if not self or not self.pOwner:
            raise ValueError('NULL pointer access')
        return look_up_account_sid(self.pOwner, system_name)

    def get_group(self, system_name=None):
        if not self or not self.pGroup:
            raise ValueError('NULL pointer access')
        return look_up_account_sid(self.pGroup, system_name)

def _check_bool(result, func, args):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return args

# msdn.microsoft.com/en-us/library/aa376399
advapi32.ConvertSidToStringSidW.errcheck = _check_bool
advapi32.ConvertSidToStringSidW.argtypes = (
    PSID, # _In_   Sid
    ctypes.POINTER(wintypes.LPWSTR)) # _Out_ StringSid

# msdn.microsoft.com/en-us/library/aa379166
advapi32.LookupAccountSidW.errcheck = _check_bool
advapi32.LookupAccountSidW.argtypes = (
    wintypes.LPCWSTR, # _In_opt_  lpSystemName
    PSID,             # _In_      lpSid
    wintypes.LPCWSTR, # _Out_opt_ lpName
    LPDWORD,          # _Inout_   cchName
    wintypes.LPCWSTR, # _Out_opt_ lpReferencedDomainName
    LPDWORD,          # _Inout_   cchReferencedDomainName
    PSID_NAME_USE)    # _Out_     peUse

# msdn.microsoft.com/en-us/library/aa446645
advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
advapi32.GetNamedSecurityInfoW.argtypes = (
    wintypes.LPWSTR,       # _In_      pObjectName
    SE_OBJECT_TYPE,        # _In_      ObjectType
    SECURITY_INFORMATION,  # _In_      SecurityInfo
    ctypes.POINTER(PSID),  # _Out_opt_ ppsidOwner
    ctypes.POINTER(PSID),  # _Out_opt_ ppsidGroup
    ctypes.POINTER(PACL),  # _Out_opt_ ppDacl
    ctypes.POINTER(PACL),  # _Out_opt_ ppSacl
    ctypes.POINTER(PSECURITY_DESCRIPTOR)) # _Out_opt_ ppSecurityDescriptor

def look_up_account_sid(sid, system_name=None):
    SIZE = 256
    name = ctypes.create_unicode_buffer(SIZE)
    domain = ctypes.create_unicode_buffer(SIZE)
    cch_name = wintypes.DWORD(SIZE)
    cch_domain = wintypes.DWORD(SIZE)
    sid_type = SID_NAME_USE()
    advapi32.LookupAccountSidW(system_name, sid, name, ctypes.byref(cch_name),
        domain, ctypes.byref(cch_domain), ctypes.byref(sid_type))
    return name.value, domain.value, sid_type

def get_file_security(filename, request=_DEFAULT_SECURITY_INFORMATION):
    # N.B. This query may fail with ERROR_INVALID_FUNCTION
    # for some filesystems.
    pSD = PSECURITY_DESCRIPTOR(needs_free=True)
    error = advapi32.GetNamedSecurityInfoW(filename, SE_FILE_OBJECT, request,
                ctypes.byref(pSD.pOwner), ctypes.byref(pSD.pGroup),
                ctypes.byref(pSD.pDacl), ctypes.byref(pSD.pSacl),
                ctypes.byref(pSD))
    if error != 0:
        raise ctypes.WinError(error)
    return pSD

def get_folder_size(folder_path):
    size = 0
    for path, dirs, files in os.walk(folder_path):
        for f in files:
            fp = os.path.join(path, f)
            size += os.path.getsize(fp)
    return size

def get_folder_info(folder_path):
    folder_name = os.path.basename(folder_path)
    folder_size_bytes = get_folder_size(folder_path)
    folder_size_megabytes = round(folder_size_bytes / (1024*1024), 2)
    folder_size_gigabytes = round(folder_size_bytes / (1024*1024*1024), 2)
    folder_modified = datetime.fromtimestamp(os.path.getmtime(folder_path))
    folder_owner = ""
    try:
        pSD = get_file_security(folder_path)
        owner_name, owner_domain, owner_sid_type = pSD.get_owner()
        folder_owner = owner_name
    except:
        pass
    
    print(folder_name, "/" , "OWNER:",folder_owner, "/" ,folder_size_bytes, "bytes /", folder_size_megabytes, "megabytes /", folder_size_gigabytes, "gigabytes /")
    return folder_name, folder_size_bytes, folder_size_megabytes, folder_size_gigabytes,folder_modified, folder_owner

def write_to_excel(folder_info_list):
    wb = Workbook()
    ws = wb.active

    # Table titles
    ws.append(["Name", "Size (bytes)", "Size (megabytes)" , "Size (gigabytes)", "Last redacted", "Owner"])

    # Writing info into a table
    for folder_info in folder_info_list:
        ws.append(folder_info)

    # Saving a table file
    wb.save("Info_info.xlsx")
    print("Done!")
    

# Folder path
root_folder = input("Inspected folder path: ")

# Info for the table
folder_info_list = []

# Loop through folder root
for folder in os.listdir(root_folder):
    try:
        folder_path = os.path.join(root_folder, folder)
        if os.path.isdir(folder_path):
            folder_info = get_folder_info(folder_path)
            folder_info_list.append(folder_info)
    except Exception as e:
        print(e)
        pass
# Writing info to an Excel table
write_to_excel(folder_info_list)
