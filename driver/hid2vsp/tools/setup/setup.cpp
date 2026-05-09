// hid2vsp_setup — installer helper. Creates / removes root-enumerated
// devnodes by HardwareID, so we don't need to ship Microsoft's devcon.exe
// (which is not redistributable per the WDK EULA).
//
// Usage:
//   hid2vsp_setup install   <inf-path> <hwid>     -> create root devnode + bind driver
//   hid2vsp_setup remove    <hwid>                -> remove all root devnodes with hwid

#include <windows.h>
#include <setupapi.h>
#include <newdev.h>
#include <cfgmgr32.h>
#include <strsafe.h>
#include <stdio.h>

#ifndef MAX_CLASS_NAME_LEN
#define MAX_CLASS_NAME_LEN 32
#endif

#pragma comment(lib, "setupapi.lib")
#pragma comment(lib, "newdev.lib")

namespace {

void Die(const wchar_t* what, DWORD err = GetLastError()) {
    wprintf(L"ERROR: %ls (Win32=%lu)\n", what, err);
    ExitProcess(err ? err : 1);
}

int CmdInstall(const wchar_t* infPath, const wchar_t* hwid) {
    GUID classGuid{};
    WCHAR className[MAX_CLASS_NAME_LEN] = {0};
    if (!SetupDiGetINFClassW(infPath, &classGuid, className, ARRAYSIZE(className), nullptr))
        Die(L"SetupDiGetINFClass");

    HDEVINFO set = SetupDiCreateDeviceInfoList(&classGuid, nullptr);
    if (set == INVALID_HANDLE_VALUE) Die(L"SetupDiCreateDeviceInfoList");

    SP_DEVINFO_DATA dev{sizeof(SP_DEVINFO_DATA)};
    if (!SetupDiCreateDeviceInfoW(set, className, &classGuid, nullptr, nullptr,
                                  DICD_GENERATE_ID, &dev))
        Die(L"SetupDiCreateDeviceInfo");

    // HardwareID is REG_MULTI_SZ — needs DOUBLE NUL termination.
    WCHAR hwidBuf[260] = {0};
    StringCchCopyW(hwidBuf, ARRAYSIZE(hwidBuf) - 1, hwid);
    DWORD hwidBytes = static_cast<DWORD>((wcslen(hwidBuf) + 2) * sizeof(WCHAR));
    if (!SetupDiSetDeviceRegistryPropertyW(set, &dev, SPDRP_HARDWAREID,
                                           reinterpret_cast<const BYTE*>(hwidBuf), hwidBytes))
        Die(L"SetupDiSetDeviceRegistryProperty(HWID)");

    if (!SetupDiCallClassInstaller(DIF_REGISTERDEVICE, set, &dev))
        Die(L"DIF_REGISTERDEVICE");

    SetupDiDestroyDeviceInfoList(set);

    BOOL reboot = FALSE;
    if (!UpdateDriverForPlugAndPlayDevicesW(nullptr, hwid, infPath, INSTALLFLAG_FORCE, &reboot))
        Die(L"UpdateDriverForPlugAndPlayDevices");

    wprintf(L"installed: %ls\n", hwid);
    return reboot ? 1 : 0;
}

int CmdRemove(const wchar_t* hwid) {
    HDEVINFO set = SetupDiGetClassDevsW(nullptr, nullptr, nullptr, DIGCF_ALLCLASSES);
    if (set == INVALID_HANDLE_VALUE) Die(L"SetupDiGetClassDevs");

    SP_DEVINFO_DATA dev{sizeof(SP_DEVINFO_DATA)};
    int removed = 0;
    for (DWORD i = 0; SetupDiEnumDeviceInfo(set, i, &dev); ++i) {
        BYTE  buf[2048] = {0};
        DWORD type = 0, sz = 0;
        if (!SetupDiGetDeviceRegistryPropertyW(set, &dev, SPDRP_HARDWAREID, &type,
                                               buf, sizeof(buf), &sz))
            continue;
        if (type != REG_MULTI_SZ && type != REG_SZ) continue;

        bool match = false;
        for (WCHAR* p = reinterpret_cast<WCHAR*>(buf); *p; p += wcslen(p) + 1) {
            if (_wcsicmp(p, hwid) == 0) { match = true; break; }
            if (type == REG_SZ) break;
        }
        if (!match) continue;

        SP_REMOVEDEVICE_PARAMS rmv{};
        rmv.ClassInstallHeader.cbSize          = sizeof(SP_CLASSINSTALL_HEADER);
        rmv.ClassInstallHeader.InstallFunction = DIF_REMOVE;
        rmv.Scope                              = DI_REMOVEDEVICE_GLOBAL;
        rmv.HwProfile                          = 0;
        if (!SetupDiSetClassInstallParamsW(set, &dev, &rmv.ClassInstallHeader, sizeof(rmv)))
            continue;
        if (SetupDiCallClassInstaller(DIF_REMOVE, set, &dev)) ++removed;
    }
    SetupDiDestroyDeviceInfoList(set);
    wprintf(L"removed: %d devnode(s) for %ls\n", removed, hwid);
    return 0;
}

void Usage() {
    wprintf(L"hid2vsp_setup install <inf-path> <hwid>\n");
    wprintf(L"hid2vsp_setup remove  <hwid>\n");
}

} // namespace

int wmain(int argc, wchar_t* argv[]) {
    if (argc < 2) { Usage(); return 2; }
    if (_wcsicmp(argv[1], L"install") == 0 && argc == 4)
        return CmdInstall(argv[2], argv[3]);
    if (_wcsicmp(argv[1], L"remove") == 0 && argc == 3)
        return CmdRemove(argv[2]);
    Usage();
    return 2;
}
