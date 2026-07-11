#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <cerrno>
#include <csignal>
#include <cstring>
#include <fcntl.h>
#include <sys/select.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace {

struct ProcessResult {
    int exit_code = 127;
    bool started = false;
    bool canceled = false;
    std::string out;
    std::string err;
};

struct BranchResult {
    std::string name;
    ProcessResult process;
    bool done = false;
    std::chrono::steady_clock::time_point finished_at{};
};

std::string executable_suffix() {
#ifdef _WIN32
    return ".exe";
#else
    return "";
#endif
}

std::string platform_id() {
#ifdef _WIN32
    return "windows";
#elif defined(__APPLE__)
    return "macos";
#else
    return "linux";
#endif
}

std::string join_command(const std::vector<std::string>& args) {
    std::ostringstream out;
    for (std::size_t i = 0; i < args.size(); ++i) {
        if (i != 0) out << ' ';
        out << args[i];
    }
    return out.str();
}

#ifdef _WIN32
std::wstring utf8_to_wide(const std::string& text) {
    if (text.empty()) return std::wstring();
    int size = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (size <= 0) {
        size = MultiByteToWideChar(CP_ACP, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
        if (size <= 0) return std::wstring(text.begin(), text.end());
        std::wstring wide(static_cast<std::size_t>(size), L'\0');
        MultiByteToWideChar(CP_ACP, 0, text.data(), static_cast<int>(text.size()), &wide[0], size);
        return wide;
    }
    std::wstring wide(static_cast<std::size_t>(size), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), &wide[0], size);
    return wide;
}

std::wstring windows_quote_arg(const std::string& arg) {
    const std::wstring wide = utf8_to_wide(arg);
    if (wide.empty()) return L"\"\"";
    bool need_quotes = false;
    for (wchar_t c : wide) {
        if (c == L' ' || c == L'\t' || c == L'\n' || c == L'\v' || c == L'"') {
            need_quotes = true;
            break;
        }
    }
    if (!need_quotes) return wide;

    std::wstring quoted = L"\"";
    int backslashes = 0;
    for (wchar_t c : wide) {
        if (c == L'\\') {
            ++backslashes;
        } else if (c == L'"') {
            quoted.append(static_cast<std::size_t>(backslashes * 2 + 1), L'\\');
            quoted.push_back(c);
            backslashes = 0;
        } else {
            quoted.append(static_cast<std::size_t>(backslashes), L'\\');
            quoted.push_back(c);
            backslashes = 0;
        }
    }
    quoted.append(static_cast<std::size_t>(backslashes * 2), L'\\');
    quoted.push_back(L'"');
    return quoted;
}

std::wstring windows_command_line(const std::vector<std::string>& args) {
    std::wstring command;
    for (std::size_t i = 0; i < args.size(); ++i) {
        if (i != 0) command.push_back(L' ');
        command += windows_quote_arg(args[i]);
    }
    return command;
}
#endif

ProcessResult run_command_capture(const std::vector<std::string>& args, std::atomic_bool& cancel) {
    ProcessResult result;
    if (args.empty()) {
        result.err = "empty command\n";
        return result;
    }

#ifdef _WIN32
    static std::mutex process_creation_mutex;
    std::unique_lock<std::mutex> creation_lock(process_creation_mutex);

    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(SECURITY_ATTRIBUTES);
    sa.lpSecurityDescriptor = nullptr;
    sa.bInheritHandle = TRUE;

    HANDLE stdout_read = nullptr;
    HANDLE stdout_write = nullptr;
    HANDLE stderr_read = nullptr;
    HANDLE stderr_write = nullptr;

    if (!CreatePipe(&stdout_read, &stdout_write, &sa, 0) ||
        !SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT, 0) ||
        !CreatePipe(&stderr_read, &stderr_write, &sa, 0) ||
        !SetHandleInformation(stderr_read, HANDLE_FLAG_INHERIT, 0)) {
        result.err = "CreatePipe failed\n";
        if (stdout_read) CloseHandle(stdout_read);
        if (stdout_write) CloseHandle(stdout_write);
        if (stderr_read) CloseHandle(stderr_read);
        if (stderr_write) CloseHandle(stderr_write);
        return result;
    }

    STARTUPINFOW startup;
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = stdout_write;
    startup.hStdError = stderr_write;

    PROCESS_INFORMATION proc;
    ZeroMemory(&proc, sizeof(proc));
    std::wstring command_line = windows_command_line(args);
    std::vector<wchar_t> mutable_command(command_line.begin(), command_line.end());
    mutable_command.push_back(L'\0');

    BOOL ok = CreateProcessW(
        nullptr,
        mutable_command.data(),
        nullptr,
        nullptr,
        TRUE,
        CREATE_NO_WINDOW,
        nullptr,
        nullptr,
        &startup,
        &proc);

    CloseHandle(stdout_write);
    CloseHandle(stderr_write);
    creation_lock.unlock();

    if (!ok) {
        DWORD error = GetLastError();
        std::ostringstream message;
        message << "CreateProcess failed (" << error << "): " << join_command(args) << "\n";
        result.err = message.str();
        CloseHandle(stdout_read);
        CloseHandle(stderr_read);
        return result;
    }

    result.started = true;

    auto read_pipe = [](HANDLE pipe, std::string* output) {
        char buffer[4096];
        DWORD read = 0;
        while (ReadFile(pipe, buffer, sizeof(buffer), &read, nullptr) && read > 0) {
            output->append(buffer, buffer + read);
        }
    };

    std::thread stdout_thread(read_pipe, stdout_read, &result.out);
    std::thread stderr_thread(read_pipe, stderr_read, &result.err);

    bool terminate_requested = false;
    for (;;) {
        DWORD wait = WaitForSingleObject(proc.hProcess, 50);
        if (wait == WAIT_OBJECT_0) break;
        if (cancel.load() && !terminate_requested) {
            TerminateProcess(proc.hProcess, 130);
            terminate_requested = true;
            result.canceled = true;
        }
    }

    DWORD exit_code = 127;
    if (GetExitCodeProcess(proc.hProcess, &exit_code)) {
        result.exit_code = static_cast<int>(exit_code);
    }

    if (stdout_thread.joinable()) stdout_thread.join();
    if (stderr_thread.joinable()) stderr_thread.join();

    CloseHandle(stdout_read);
    CloseHandle(stderr_read);
    CloseHandle(proc.hThread);
    CloseHandle(proc.hProcess);
    return result;
#else
    int stdout_pipe[2] = {-1, -1};
    int stderr_pipe[2] = {-1, -1};
    if (pipe(stdout_pipe) != 0 || pipe(stderr_pipe) != 0) {
        result.err = std::string("pipe failed: ") + std::strerror(errno) + "\n";
        if (stdout_pipe[0] != -1) close(stdout_pipe[0]);
        if (stdout_pipe[1] != -1) close(stdout_pipe[1]);
        if (stderr_pipe[0] != -1) close(stderr_pipe[0]);
        if (stderr_pipe[1] != -1) close(stderr_pipe[1]);
        return result;
    }

    pid_t pid = fork();
    if (pid < 0) {
        result.err = std::string("fork failed: ") + std::strerror(errno) + "\n";
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        close(stderr_pipe[0]);
        close(stderr_pipe[1]);
        return result;
    }

    if (pid == 0) {
        setpgid(0, 0);
        dup2(stdout_pipe[1], STDOUT_FILENO);
        dup2(stderr_pipe[1], STDERR_FILENO);
        close(stdout_pipe[0]);
        close(stdout_pipe[1]);
        close(stderr_pipe[0]);
        close(stderr_pipe[1]);

        std::vector<char*> argv;
        argv.reserve(args.size() + 1);
        for (const std::string& arg : args) argv.push_back(const_cast<char*>(arg.c_str()));
        argv.push_back(nullptr);
        execvp(argv[0], argv.data());
        std::string message = "exec failed: " + args[0] + "\n";
        write(STDERR_FILENO, message.data(), message.size());
        _exit(127);
    }

    result.started = true;
    close(stdout_pipe[1]);
    close(stderr_pipe[1]);
    fcntl(stdout_pipe[0], F_SETFL, fcntl(stdout_pipe[0], F_GETFL, 0) | O_NONBLOCK);
    fcntl(stderr_pipe[0], F_SETFL, fcntl(stderr_pipe[0], F_GETFL, 0) | O_NONBLOCK);

    bool stdout_open = true;
    bool stderr_open = true;
    bool exited = false;
    bool terminate_requested = false;
    auto terminate_time = std::chrono::steady_clock::now();
    int status = 0;

    auto read_available = [](int fd, std::string& output, bool& open) {
        char buffer[4096];
        for (;;) {
            ssize_t n = read(fd, buffer, sizeof(buffer));
            if (n > 0) {
                output.append(buffer, buffer + n);
            } else if (n == 0) {
                open = false;
                close(fd);
                break;
            } else {
                if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                open = false;
                close(fd);
                break;
            }
        }
    };

    while (stdout_open || stderr_open || !exited) {
        if (!exited) {
            pid_t waited = waitpid(pid, &status, WNOHANG);
            if (waited == pid) exited = true;
        }

        if (cancel.load() && !terminate_requested && !exited) {
            kill(-pid, SIGTERM);
            terminate_requested = true;
            terminate_time = std::chrono::steady_clock::now();
            result.canceled = true;
        }
        if (terminate_requested && !exited) {
            const auto elapsed = std::chrono::steady_clock::now() - terminate_time;
            if (elapsed > std::chrono::seconds(2)) {
                kill(-pid, SIGKILL);
            }
        }

        fd_set read_set;
        FD_ZERO(&read_set);
        int max_fd = -1;
        if (stdout_open) {
            FD_SET(stdout_pipe[0], &read_set);
            max_fd = std::max(max_fd, stdout_pipe[0]);
        }
        if (stderr_open) {
            FD_SET(stderr_pipe[0], &read_set);
            max_fd = std::max(max_fd, stderr_pipe[0]);
        }
        timeval timeout;
        timeout.tv_sec = 0;
        timeout.tv_usec = 50000;
        int ready = max_fd >= 0 ? select(max_fd + 1, &read_set, nullptr, nullptr, &timeout) : 0;
        if (ready > 0) {
            if (stdout_open && FD_ISSET(stdout_pipe[0], &read_set)) {
                read_available(stdout_pipe[0], result.out, stdout_open);
            }
            if (stderr_open && FD_ISSET(stderr_pipe[0], &read_set)) {
                read_available(stderr_pipe[0], result.err, stderr_open);
            }
        } else {
            if (stdout_open) read_available(stdout_pipe[0], result.out, stdout_open);
            if (stderr_open) read_available(stderr_pipe[0], result.err, stderr_open);
        }

        if (exited && !stdout_open && !stderr_open) break;
    }

    if (!exited) waitpid(pid, &status, 0);
    if (WIFEXITED(status)) result.exit_code = WEXITSTATUS(status);
    else if (WIFSIGNALED(status)) result.exit_code = 128 + WTERMSIG(status);
    else result.exit_code = 127;
    return result;
#endif
}

bool starts_with_dash(const std::string& arg) {
    return !arg.empty() && arg[0] == '-';
}

bool is_input_option(const std::string& arg) {
    return arg == "--pd-code" || arg == "-c" || arg == "--pd-file" || arg == "-f" ||
           arg == "--pd-dir" || arg == "-d";
}

bool is_value_option(const std::string& arg) {
    return arg == "--threads" || arg == "-j";
}

bool is_simplify_control_option(const std::string& arg) {
    return arg == "--simplify-pd" || arg == "--no-simplify-pd" || arg == "--raw-pd";
}

struct Config {
    std::vector<std::string> direct_args;
    std::vector<std::string> simplify_args;
    std::vector<std::string> kh_after_simplify_args;
    std::string cppkh_exe;
    std::string pd_simplify_exe;
    bool help = false;
    bool quick_help = false;
};

std::string require_value(int& i, int argc, char** argv, const std::string& option) {
    if (i + 1 >= argc) throw std::runtime_error(option + " requires a value");
    ++i;
    return argv[i];
}

Config parse_args(int argc, char** argv) {
    Config cfg;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--quick-cppkh-help") {
            cfg.quick_help = true;
        } else if (arg == "--cppkh-exe" || arg == "--quick-cppkh-cppkh-exe") {
            cfg.cppkh_exe = require_value(i, argc, argv, arg);
        } else if (arg == "--pd-simplify-exe" || arg == "--quick-cppkh-pd-simplify-exe") {
            cfg.pd_simplify_exe = require_value(i, argc, argv, arg);
        } else if (is_input_option(arg)) {
            const std::string value = require_value(i, argc, argv, arg);
            cfg.direct_args.push_back(arg);
            cfg.direct_args.push_back(value);
            cfg.simplify_args.push_back(arg);
            cfg.simplify_args.push_back(value);
        } else if (is_value_option(arg)) {
            const std::string value = require_value(i, argc, argv, arg);
            cfg.direct_args.push_back(arg);
            cfg.direct_args.push_back(value);
            cfg.kh_after_simplify_args.push_back(arg);
            cfg.kh_after_simplify_args.push_back(value);
        } else {
            if (arg == "--help" || arg == "-h") cfg.help = true;
            cfg.direct_args.push_back(arg);
            if (!starts_with_dash(arg)) {
                cfg.simplify_args.push_back(arg);
            } else if (!is_simplify_control_option(arg)) {
                cfg.kh_after_simplify_args.push_back(arg);
            }
        }
    }
    return cfg;
}

fs::path path_from_env(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || *value == '\0') return fs::path();
    return fs::path(value);
}

fs::path find_on_path(const std::string& name) {
    const char* path_value = std::getenv("PATH");
    if (path_value == nullptr) return fs::path();
    std::string path_text(path_value);
#ifdef _WIN32
    const char separator = ';';
#else
    const char separator = ':';
#endif
    std::size_t start = 0;
    while (start <= path_text.size()) {
        std::size_t end = path_text.find(separator, start);
        if (end == std::string::npos) end = path_text.size();
        fs::path dir = path_text.substr(start, end - start);
        fs::path candidate = dir / name;
        if (fs::exists(candidate)) return fs::absolute(candidate);
        start = end + 1;
    }
    return fs::path();
}

std::string resolve_tool(
    const std::string& explicit_path,
    const char* env_name,
    const std::string& binary_name,
    const fs::path& self_dir) {
    std::vector<fs::path> candidates;
    if (!explicit_path.empty()) candidates.push_back(fs::path(explicit_path));
    fs::path env_path = path_from_env(env_name);
    if (!env_path.empty()) candidates.push_back(env_path);
    candidates.push_back(self_dir / binary_name);
    candidates.push_back(fs::current_path() / "dist" / platform_id() / binary_name);
    candidates.push_back(fs::current_path() / "build" / "bin" / binary_name);

    for (const fs::path& candidate : candidates) {
        if (candidate.empty()) continue;
        if (fs::exists(candidate)) return fs::absolute(candidate).string();
    }

    fs::path from_path = find_on_path(binary_name);
    if (!from_path.empty()) return from_path.string();

    if (!explicit_path.empty()) {
        throw std::runtime_error("tool not found: " + explicit_path);
    }
    throw std::runtime_error(std::string("could not find ") + binary_name +
                             " (set " + env_name + " or pass the quick_cppkh tool option)");
}

std::string json_unescape_string(const std::string& text, std::size_t& pos) {
    if (pos >= text.size() || text[pos] != '"') throw std::runtime_error("expected JSON string");
    ++pos;
    std::string out;
    while (pos < text.size()) {
        char c = text[pos++];
        if (c == '"') return out;
        if (c != '\\') {
            out.push_back(c);
            continue;
        }
        if (pos >= text.size()) break;
        char esc = text[pos++];
        switch (esc) {
            case '"':
            case '\\':
            case '/':
                out.push_back(esc);
                break;
            case 'b':
                out.push_back('\b');
                break;
            case 'f':
                out.push_back('\f');
                break;
            case 'n':
                out.push_back('\n');
                break;
            case 'r':
                out.push_back('\r');
                break;
            case 't':
                out.push_back('\t');
                break;
            case 'u':
                // PD codes are ASCII; preserve unknown Unicode escapes literally.
                out += "\\u";
                for (int i = 0; i < 4 && pos < text.size(); ++i) out.push_back(text[pos++]);
                break;
            default:
                out.push_back(esc);
                break;
        }
    }
    throw std::runtime_error("unterminated JSON string");
}

std::vector<std::string> extract_final_pd_codes(const std::string& json) {
    std::vector<std::string> codes;
    const std::string key = "\"final_pd_code\"";
    std::size_t pos = 0;
    while ((pos = json.find(key, pos)) != std::string::npos) {
        pos += key.size();
        while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t' || json[pos] == '\r' || json[pos] == '\n')) ++pos;
        if (pos >= json.size() || json[pos] != ':') continue;
        ++pos;
        while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t' || json[pos] == '\r' || json[pos] == '\n')) ++pos;
        if (pos < json.size() && json[pos] == '"') {
            codes.push_back(json_unescape_string(json, pos));
        }
    }
    if (!codes.empty()) return codes;

    const std::string text_key = "final_pd_code:";
    pos = 0;
    while ((pos = json.find(text_key, pos)) != std::string::npos) {
        pos += text_key.size();
        while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t')) ++pos;
        std::size_t end = json.find_first_of("\r\n", pos);
        std::string line = json.substr(pos, end == std::string::npos ? std::string::npos : end - pos);
        if (!line.empty()) codes.push_back(line);
        if (end == std::string::npos) break;
        pos = end + 1;
    }
    return codes;
}

fs::path make_temp_pd_file(const std::vector<std::string>& codes) {
    static std::atomic<unsigned long long> counter{0};
    const auto now = std::chrono::steady_clock::now().time_since_epoch().count();
    fs::path path = fs::temp_directory_path() /
                    ("quick_cppkh_" + std::to_string(now) + "_" +
                     std::to_string(counter.fetch_add(1)) + ".pd");
    std::ofstream out(path, std::ios::out | std::ios::binary);
    if (!out) throw std::runtime_error("could not create temporary PD file: " + path.string());
    for (const std::string& code : codes) out << code << '\n';
    return path;
}

BranchResult run_direct_branch(const Config& cfg, std::atomic_bool& cancel) {
    BranchResult branch;
    branch.name = "direct cppkh";
    std::vector<std::string> command;
    command.push_back(cfg.cppkh_exe);
    command.insert(command.end(), cfg.direct_args.begin(), cfg.direct_args.end());
    branch.process = run_command_capture(command, cancel);
    branch.done = true;
    branch.finished_at = std::chrono::steady_clock::now();
    return branch;
}

BranchResult run_simplified_branch(const Config& cfg, std::atomic_bool& cancel) {
    BranchResult branch;
    branch.name = "simplify then cppkh";

    std::vector<std::string> simplify_command;
    simplify_command.push_back(cfg.pd_simplify_exe);
    simplify_command.insert(simplify_command.end(), cfg.simplify_args.begin(), cfg.simplify_args.end());
    simplify_command.push_back("--json");

    ProcessResult simplify = run_command_capture(simplify_command, cancel);
    if (cancel.load()) {
        branch.process = simplify;
        branch.done = true;
        branch.finished_at = std::chrono::steady_clock::now();
        return branch;
    }

    std::vector<std::string> codes;
    try {
        codes = extract_final_pd_codes(simplify.out);
    } catch (const std::exception& error) {
        simplify.err += std::string("quick_cppkh: failed to parse pd_simplify JSON: ") + error.what() + "\n";
    }

    if (codes.empty()) {
        branch.process = simplify;
        if (branch.process.exit_code == 0) branch.process.exit_code = 2;
        branch.process.err += "quick_cppkh: pd_simplify produced no final_pd_code\n";
        branch.done = true;
        branch.finished_at = std::chrono::steady_clock::now();
        return branch;
    }

    fs::path temp_file;
    try {
        temp_file = make_temp_pd_file(codes);
    } catch (const std::exception& error) {
        branch.process = simplify;
        branch.process.exit_code = 2;
        branch.process.err += std::string("quick_cppkh: ") + error.what() + "\n";
        branch.done = true;
        branch.finished_at = std::chrono::steady_clock::now();
        return branch;
    }

    std::vector<std::string> kh_command;
    kh_command.push_back(cfg.cppkh_exe);
    kh_command.insert(kh_command.end(), cfg.kh_after_simplify_args.begin(), cfg.kh_after_simplify_args.end());
    kh_command.push_back("--no-simplify-pd");
    kh_command.push_back("--pd-file");
    kh_command.push_back(temp_file.string());

    ProcessResult kh = run_command_capture(kh_command, cancel);
    kh.err = simplify.err + kh.err;
    branch.process = std::move(kh);

    std::error_code ec;
    fs::remove(temp_file, ec);

    branch.done = true;
    branch.finished_at = std::chrono::steady_clock::now();
    return branch;
}

void replay_result(const ProcessResult& result) {
    if (!result.out.empty()) {
        std::cout << result.out;
        std::cout.flush();
    }
    if (!result.err.empty()) {
        std::cerr << result.err;
        std::cerr.flush();
    }
}

void print_quick_help() {
    std::cout
        << "Usage: quick_cppkh [cppkh options]\n"
        << "\n"
        << "quick_cppkh accepts the cppkh CLI and races two routes:\n"
        << "  1. cppkh on the original input.\n"
        << "  2. pd_simplify on the input, then cppkh --no-simplify-pd.\n"
        << "\n"
        << "Extra tool-location options:\n"
        << "  --cppkh-exe PATH                 Use this cppkh executable.\n"
        << "  --pd-simplify-exe PATH           Use this pd_simplify executable.\n"
        << "  --quick-cppkh-help               Show this help.\n"
        << "\n"
        << "Environment:\n"
        << "  QUICK_CPPKH_CPPKH                Default cppkh executable path.\n"
        << "  QUICK_CPPKH_PD_SIMPLIFY          Default pd_simplify executable path.\n";
}

int run_help_passthrough(const Config& cfg) {
    std::atomic_bool cancel(false);
    std::vector<std::string> command;
    command.push_back(cfg.cppkh_exe);
    command.insert(command.end(), cfg.direct_args.begin(), cfg.direct_args.end());
    ProcessResult result = run_command_capture(command, cancel);
    replay_result(result);
    return result.exit_code;
}

int race_branches(const Config& cfg) {
    std::atomic_bool cancel(false);
    std::mutex mutex;
    std::condition_variable cv;
    BranchResult direct;
    BranchResult simplified;
    int finished = 0;

    auto run_and_store = [&](bool direct_branch) {
        BranchResult result = direct_branch ? run_direct_branch(cfg, cancel)
                                            : run_simplified_branch(cfg, cancel);
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (direct_branch) direct = std::move(result);
            else simplified = std::move(result);
            ++finished;
        }
        cv.notify_one();
    };

    std::thread direct_thread(run_and_store, true);
    std::thread simplified_thread(run_and_store, false);

    BranchResult* winner = nullptr;
    {
        std::unique_lock<std::mutex> lock(mutex);
        while (winner == nullptr && finished < 2) {
            cv.wait(lock);
            std::vector<BranchResult*> successes;
            if (direct.done && !direct.process.canceled && direct.process.exit_code == 0) {
                successes.push_back(&direct);
            }
            if (simplified.done && !simplified.process.canceled && simplified.process.exit_code == 0) {
                successes.push_back(&simplified);
            }
            for (BranchResult* candidate : successes) {
                if (winner == nullptr || candidate->finished_at < winner->finished_at) {
                    winner = candidate;
                }
            }
            if (winner != nullptr) cancel.store(true);
        }
        if (winner == nullptr && finished == 2) {
            if (direct.done && !direct.process.canceled && direct.process.exit_code == 0) winner = &direct;
            if (simplified.done && !simplified.process.canceled && simplified.process.exit_code == 0 &&
                (winner == nullptr || simplified.finished_at < winner->finished_at)) {
                winner = &simplified;
            }
        }
    }

    if (winner != nullptr) cancel.store(true);

    if (direct_thread.joinable()) direct_thread.join();
    if (simplified_thread.joinable()) simplified_thread.join();

    if (winner != nullptr) {
        replay_result(winner->process);
        return winner->process.exit_code;
    }

    std::cerr << "quick_cppkh: both computation routes failed\n";
    if (direct.done) {
        std::cerr << "\n[direct cppkh]\n";
        replay_result(direct.process);
    }
    if (simplified.done) {
        std::cerr << "\n[simplify then cppkh]\n";
        replay_result(simplified.process);
    }
    if (direct.done) return direct.process.exit_code == 0 ? 2 : direct.process.exit_code;
    if (simplified.done) return simplified.process.exit_code == 0 ? 2 : simplified.process.exit_code;
    return 2;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        Config cfg = parse_args(argc, argv);
        if (cfg.quick_help) {
            print_quick_help();
            return 0;
        }

        fs::path self = argc > 0 ? fs::absolute(fs::path(argv[0])) : fs::current_path();
        fs::path self_dir = fs::is_directory(self) ? self : self.parent_path();
        const std::string suffix = executable_suffix();
        cfg.cppkh_exe = resolve_tool(cfg.cppkh_exe, "QUICK_CPPKH_CPPKH", "cppkh" + suffix, self_dir);

        if (cfg.help) return run_help_passthrough(cfg);
        cfg.pd_simplify_exe = resolve_tool(
            cfg.pd_simplify_exe,
            "QUICK_CPPKH_PD_SIMPLIFY",
            "pd_simplify" + suffix,
            self_dir);
        return race_branches(cfg);
    } catch (const std::exception& error) {
        std::cerr << "quick_cppkh: error: " << error.what() << "\n";
        return 2;
    }
}
