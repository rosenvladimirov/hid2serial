// hid2vsp tray applet — shows current pair_a/pair_b -> COMx mappings.
// Status only: no admin required, no driver actions.

#include <windows.h>
#include <shellapi.h>
#include <cfgmgr32.h>
#include <strsafe.h>
#include <algorithm>
#include <vector>
#include <string>

#pragma comment(lib, "cfgmgr32.lib")

#include "resource.h"

#define WM_TRAY_ICON (WM_APP + 1)

namespace {

constexpr wchar_t kClassName[]  = L"hid2vsp_tray_wnd";
constexpr wchar_t kWindowTitle[] = L"hid2vsp tray";
constexpr wchar_t kEnumRoot[]   = L"SYSTEM\\CurrentControlSet\\Enum\\Root\\PORTS";

enum class PairState { Unknown, Started, Disabled, Problem };

struct Pair {
    std::wstring side;     // "A" or "B"
    std::wstring instance; // e.g. "0000004a" (registry subkey under Root\PORTS)
    std::wstring port;     // e.g. "COM3" (empty if unresolved)
    PairState    state = PairState::Unknown;
};

const wchar_t* StateLabel(PairState s) {
    switch (s) {
        case PairState::Started:  return L"OK";
        case PairState::Disabled: return L"DISABLED";
        case PairState::Problem:  return L"PROBLEM";
        default:                  return L"?";
    }
}

PairState QueryPairState(const std::wstring& instance) {
    wchar_t full[256];
    StringCchPrintfW(full, 256, L"ROOT\\PORTS\\%s", instance.c_str());
    DEVINST dn = 0;
    if (CM_Locate_DevNodeW(&dn, full, CM_LOCATE_DEVNODE_NORMAL) != CR_SUCCESS)
        return PairState::Unknown;
    ULONG status = 0, problem = 0;
    if (CM_Get_DevNode_Status(&status, &problem, dn, 0) != CR_SUCCESS)
        return PairState::Unknown;
    if (status & DN_HAS_PROBLEM) {
        if (problem == CM_PROB_DISABLED) return PairState::Disabled;
        return PairState::Problem;
    }
    if (status & DN_STARTED) return PairState::Started;
    return PairState::Unknown;
}

NOTIFYICONDATAW g_nid{};
HWND            g_hwnd = nullptr;
std::vector<Pair> g_pairs;

bool MultiSzContains(const std::wstring& haystack, const wchar_t* needle) {
    // REG_MULTI_SZ comes back NUL-separated; wstring keeps embedded NULs.
    const size_t nlen = wcslen(needle);
    for (size_t i = 0; i + nlen <= haystack.size(); ++i) {
        if (_wcsnicmp(haystack.c_str() + i, needle, nlen) == 0) return true;
    }
    return false;
}

std::wstring ReadMultiSz(HKEY key, const wchar_t* name) {
    DWORD type = 0, cb = 0;
    if (RegQueryValueExW(key, name, nullptr, &type, nullptr, &cb) != ERROR_SUCCESS) return {};
    if (type != REG_MULTI_SZ && type != REG_SZ) return {};
    std::wstring buf(cb / sizeof(wchar_t), L'\0');
    if (RegQueryValueExW(key, name, nullptr, &type,
                         reinterpret_cast<BYTE*>(buf.data()), &cb) != ERROR_SUCCESS) return {};
    return buf;
}

std::wstring ReadSz(HKEY key, const wchar_t* name) {
    DWORD type = 0, cb = 0;
    if (RegQueryValueExW(key, name, nullptr, &type, nullptr, &cb) != ERROR_SUCCESS) return {};
    if (type != REG_SZ) return {};
    std::wstring buf(cb / sizeof(wchar_t), L'\0');
    if (RegQueryValueExW(key, name, nullptr, &type,
                         reinterpret_cast<BYTE*>(buf.data()), &cb) != ERROR_SUCCESS) return {};
    while (!buf.empty() && buf.back() == L'\0') buf.pop_back();
    return buf;
}

void RefreshPairs() {
    g_pairs.clear();

    HKEY root = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, kEnumRoot, 0, KEY_READ, &root) != ERROR_SUCCESS) return;

    wchar_t name[256];
    DWORD nameLen;
    for (DWORD i = 0;; ++i) {
        nameLen = 256;
        LONG rc = RegEnumKeyExW(root, i, name, &nameLen, nullptr, nullptr, nullptr, nullptr);
        if (rc == ERROR_NO_MORE_ITEMS) break;
        if (rc != ERROR_SUCCESS) continue;

        HKEY inst = nullptr;
        if (RegOpenKeyExW(root, name, 0, KEY_READ, &inst) != ERROR_SUCCESS) continue;

        std::wstring hwids = ReadMultiSz(inst, L"HardwareID");
        const wchar_t* side = nullptr;
        if      (MultiSzContains(hwids, L"hid2vsp_pair_a")) side = L"A";
        else if (MultiSzContains(hwids, L"hid2vsp_pair_b")) side = L"B";

        if (side) {
            HKEY params = nullptr;
            std::wstring port;
            if (RegOpenKeyExW(inst, L"Device Parameters", 0, KEY_READ, &params) == ERROR_SUCCESS) {
                port = ReadSz(params, L"PortName");
                RegCloseKey(params);
            }
            Pair p{side, name, port};
            p.state = QueryPairState(p.instance);
            g_pairs.push_back(std::move(p));
        }
        RegCloseKey(inst);
    }
    RegCloseKey(root);

    // Sort: A before B, then by instance.
    std::sort(g_pairs.begin(), g_pairs.end(), [](const Pair& a, const Pair& b) {
        if (a.side != b.side) return a.side < b.side;
        return a.instance < b.instance;
    });
}

void ShowMenu(HWND hwnd) {
    POINT pt;
    GetCursorPos(&pt);

    HMENU menu = CreatePopupMenu();
    if (g_pairs.empty()) {
        AppendMenuW(menu, MF_STRING | MF_GRAYED, 0, L"No hid2vsp pairs found");
    } else {
        UINT id = IDM_PAIR_FIRST;
        for (const auto& p : g_pairs) {
            wchar_t line[128];
            const wchar_t* port = p.port.empty() ? L"(no PortName)" : p.port.c_str();
            StringCchPrintfW(line, 128, L"pair_%s  ->  %s  [%s]",
                             p.side.c_str(), port, StateLabel(p.state));
            AppendMenuW(menu, MF_STRING | MF_GRAYED, id++, line);
            if (id > IDM_PAIR_LAST) break;
        }
    }
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, IDM_REFRESH,     L"Refresh");
    AppendMenuW(menu, MF_STRING, IDM_RECONNECT,   L"Reconnect (admin)");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, IDM_OPEN_FOLDER, L"Open install folder");
    AppendMenuW(menu, MF_STRING, IDM_OPEN_LOG,    L"Open install log");
    AppendMenuW(menu, MF_SEPARATOR, 0, nullptr);
    AppendMenuW(menu, MF_STRING, IDM_EXIT,        L"Exit");

    SetForegroundWindow(hwnd);
    TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
                   pt.x, pt.y, 0, hwnd, nullptr);
    PostMessageW(hwnd, WM_NULL, 0, 0);  // dismiss menu cleanly per MS docs
    DestroyMenu(menu);
}

std::wstring InstallDir() {
    WCHAR path[MAX_PATH] = {0};
    GetModuleFileNameW(nullptr, path, MAX_PATH);
    if (WCHAR* slash = wcsrchr(path, L'\\')) *slash = L'\0';
    return path;
}

void OpenInstallFolder() {
    ShellExecuteW(nullptr, L"open", InstallDir().c_str(), nullptr, nullptr, SW_SHOWNORMAL);
}

void OpenInstallLog() {
    std::wstring log = InstallDir() + L"\\install.log";
    if (GetFileAttributesW(log.c_str()) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(nullptr, L"install.log not found in install folder.\n"
                             L"Re-run setup with /LOG to capture one.",
                    L"hid2vsp", MB_OK | MB_ICONINFORMATION);
        return;
    }
    ShellExecuteW(nullptr, L"open", L"notepad.exe", log.c_str(), nullptr, SW_SHOWNORMAL);
}

void RelaunchAsAdmin(const wchar_t* args) {
    WCHAR exe[MAX_PATH] = {0};
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    SHELLEXECUTEINFOW sei{sizeof(sei)};
    sei.fMask  = SEE_MASK_NOCLOSEPROCESS;
    sei.lpVerb = L"runas";
    sei.lpFile = exe;
    sei.lpParameters = args;
    sei.nShow  = SW_HIDE;
    if (ShellExecuteExW(&sei) && sei.hProcess) {
        WaitForSingleObject(sei.hProcess, 30000);
        CloseHandle(sei.hProcess);
    }
}

// Cycle a single root devnode (disable + enable). Returns true on success.
bool CyclePairElevated(const std::wstring& instance) {
    wchar_t full[256];
    StringCchPrintfW(full, 256, L"ROOT\\PORTS\\%s", instance.c_str());
    DEVINST dn = 0;
    if (CM_Locate_DevNodeW(&dn, full, CM_LOCATE_DEVNODE_NORMAL) != CR_SUCCESS) return false;
    if (CM_Disable_DevNode(dn, 0) != CR_SUCCESS) return false;
    if (CM_Enable_DevNode(dn, 0)  != CR_SUCCESS) return false;
    return true;
}

int ReconnectAllElevated() {
    // Re-enumerate (we may be a fresh elevated process — no g_pairs yet).
    g_pairs.clear();
    HKEY root = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, kEnumRoot, 0, KEY_READ, &root) != ERROR_SUCCESS) return 1;
    wchar_t name[256];
    DWORD nameLen;
    int cycled = 0;
    for (DWORD i = 0;; ++i) {
        nameLen = 256;
        if (RegEnumKeyExW(root, i, name, &nameLen, nullptr, nullptr, nullptr, nullptr) != ERROR_SUCCESS) break;
        HKEY inst = nullptr;
        if (RegOpenKeyExW(root, name, 0, KEY_READ, &inst) != ERROR_SUCCESS) continue;
        std::wstring hwids = ReadMultiSz(inst, L"HardwareID");
        bool ours = MultiSzContains(hwids, L"hid2vsp_pair_a") ||
                    MultiSzContains(hwids, L"hid2vsp_pair_b");
        RegCloseKey(inst);
        if (ours && CyclePairElevated(name)) ++cycled;
    }
    RegCloseKey(root);
    return cycled > 0 ? 0 : 2;
}

void RefreshTooltipText() {
    int countA = 0, countB = 0;
    for (const auto& p : g_pairs) {
        if (p.side == L"A") ++countA;
        else if (p.side == L"B") ++countB;
    }
    StringCchPrintfW(g_nid.szTip, ARRAYSIZE(g_nid.szTip),
                     L"hid2vsp - A:%d  B:%d", countA, countB);
}

void NotifyTooltipChanged() {
    NOTIFYICONDATAW mod = g_nid;
    mod.uFlags = NIF_TIP;
    Shell_NotifyIconW(NIM_MODIFY, &mod);
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_TRAY_ICON:
            if (LOWORD(lp) == WM_RBUTTONUP || LOWORD(lp) == WM_CONTEXTMENU ||
                LOWORD(lp) == WM_LBUTTONUP) {
                RefreshPairs();
                RefreshTooltipText();
                NotifyTooltipChanged();
                ShowMenu(hwnd);
            }
            return 0;
        case WM_COMMAND:
            switch (LOWORD(wp)) {
                case IDM_REFRESH:
                    RefreshPairs();
                    RefreshTooltipText();
                    NotifyTooltipChanged();
                    return 0;
                case IDM_RECONNECT:
                    RelaunchAsAdmin(L"--reconnect");
                    RefreshPairs();
                    RefreshTooltipText();
                    NotifyTooltipChanged();
                    return 0;
                case IDM_OPEN_FOLDER:
                    OpenInstallFolder();
                    return 0;
                case IDM_OPEN_LOG:
                    OpenInstallLog();
                    return 0;
                case IDM_EXIT:
                    DestroyWindow(hwnd);
                    return 0;
            }
            return 0;
        case WM_DESTROY:
            Shell_NotifyIconW(NIM_DELETE, &g_nid);
            PostQuitMessage(0);
            return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

} // namespace

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE, LPWSTR cmdLine, int) {
    // Sub-mode: relaunched elevated to cycle the devnodes, then exit.
    if (cmdLine && wcsstr(cmdLine, L"--reconnect")) {
        return ReconnectAllElevated();
    }

    // Single-instance: bail out if another tray is already running.
    HANDLE mtx = CreateMutexW(nullptr, TRUE, L"Global\\hid2vsp_tray_singleton");
    if (mtx && GetLastError() == ERROR_ALREADY_EXISTS) return 0;

    WNDCLASSW wc{};
    wc.lpfnWndProc   = WndProc;
    wc.hInstance     = hInst;
    wc.lpszClassName = kClassName;
    RegisterClassW(&wc);

    // Top-level (not HWND_MESSAGE) — TrackPopupMenu / SetForegroundWindow
    // need a real top-level window even if it's never shown.
    g_hwnd = CreateWindowExW(WS_EX_TOOLWINDOW, kClassName, kWindowTitle,
                             WS_POPUP, 0, 0, 0, 0,
                             nullptr, nullptr, hInst, nullptr);
    if (!g_hwnd) return 1;

    g_nid.cbSize           = sizeof(g_nid);
    g_nid.hWnd             = g_hwnd;
    g_nid.uID              = 1;
    g_nid.uFlags           = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    g_nid.uCallbackMessage = WM_TRAY_ICON;
    g_nid.hIcon = LoadIconW(hInst, MAKEINTRESOURCEW(IDI_TRAY));
    if (!g_nid.hIcon) {
        // Fallback: load from tray.ico next to the exe.
        WCHAR exePath[MAX_PATH] = {0};
        GetModuleFileNameW(nullptr, exePath, MAX_PATH);
        if (WCHAR* p = wcsrchr(exePath, L'\\')) {
            *(p + 1) = L'\0';
            wcscat_s(exePath, MAX_PATH, L"hid2vsp.ico");
            g_nid.hIcon = static_cast<HICON>(LoadImageW(nullptr, exePath, IMAGE_ICON,
                                                        0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE));
        }
    }
    if (!g_nid.hIcon) g_nid.hIcon = LoadIconW(nullptr, IDI_APPLICATION);

    RefreshPairs();
    RefreshTooltipText();

    Shell_NotifyIconW(NIM_ADD, &g_nid);

    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return 0;
}
