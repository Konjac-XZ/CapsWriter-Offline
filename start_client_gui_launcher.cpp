#include <windows.h>

#include <filesystem>
#include <string>

namespace {

std::wstring quote_arg(const std::wstring& value) {
    std::wstring quoted = L"\"";
    quoted += value;
    quoted += L"\"";
    return quoted;
}

bool run_taskkill_tree(DWORD pid, const std::filesystem::path& work_dir) {
    std::wstring command_line =
        L"taskkill /PID " + std::to_wstring(pid) + L" /T /F";

    STARTUPINFOW startup_info{};
    startup_info.cb = sizeof(startup_info);
    startup_info.dwFlags = STARTF_USESHOWWINDOW;
    startup_info.wShowWindow = SW_HIDE;

    PROCESS_INFORMATION process_info{};
    std::wstring mutable_command_line = command_line;
    const BOOL created = CreateProcessW(
        nullptr,
        mutable_command_line.data(),
        nullptr,
        nullptr,
        FALSE,
        CREATE_NO_WINDOW,
        nullptr,
        work_dir.c_str(),
        &startup_info,
        &process_info);

    if (!created) {
        return false;
    }

    WaitForSingleObject(process_info.hProcess, 5000);
    DWORD exit_code = 1;
    GetExitCodeProcess(process_info.hProcess, &exit_code);

    CloseHandle(process_info.hThread);
    CloseHandle(process_info.hProcess);

    return exit_code == 0 || exit_code == 128;
}

void kill_running_script_instances(const std::filesystem::path& work_dir) {
    std::wstring command_line =
        L"powershell -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command \""
        L"Get-CimInstance Win32_Process | "
        L"Where-Object { $_.CommandLine -like '*start_client_gui.py*' } | "
        L"ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        L"\"";

    STARTUPINFOW startup_info{};
    startup_info.cb = sizeof(startup_info);
    startup_info.dwFlags = STARTF_USESHOWWINDOW;
    startup_info.wShowWindow = SW_HIDE;

    PROCESS_INFORMATION process_info{};
    std::wstring mutable_command_line = command_line;
    const BOOL created = CreateProcessW(
        nullptr,
        mutable_command_line.data(),
        nullptr,
        nullptr,
        FALSE,
        CREATE_NO_WINDOW,
        nullptr,
        work_dir.c_str(),
        &startup_info,
        &process_info);

    if (!created) {
        return;
    }

    WaitForSingleObject(process_info.hProcess, 10000);
    CloseHandle(process_info.hThread);
    CloseHandle(process_info.hProcess);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    wchar_t module_path[MAX_PATH] = {0};
    const DWORD module_len = GetModuleFileNameW(nullptr, module_path, MAX_PATH);
    if (module_len == 0 || module_len >= MAX_PATH) {
        return 1;
    }

    std::filesystem::path exe_path(module_path);
    const std::filesystem::path work_dir = exe_path.parent_path();
    const std::filesystem::path script_path = work_dir / L"start_client_gui.py";

    kill_running_script_instances(work_dir);

    std::wstring command_line =
        L"uv run python " + quote_arg(script_path.wstring());

    STARTUPINFOW startup_info{};
    startup_info.cb = sizeof(startup_info);
    startup_info.dwFlags = STARTF_USESHOWWINDOW;
    startup_info.wShowWindow = SW_HIDE;

    PROCESS_INFORMATION process_info{};
    std::wstring mutable_command_line = command_line;
    const BOOL created = CreateProcessW(
        nullptr,
        mutable_command_line.data(),
        nullptr,
        nullptr,
        FALSE,
        CREATE_NO_WINDOW,
        nullptr,
        work_dir.c_str(),
        &startup_info,
        &process_info);

    if (!created) {
        return static_cast<int>(GetLastError());
    }

    CloseHandle(process_info.hThread);
    CloseHandle(process_info.hProcess);
    return 0;
}