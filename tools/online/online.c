/*
 * Dark Colony Ultimate - the ONLINE WAR module (fix `online`; DC16_DISPLAY_AND_RESOLUTION.md 10.51,
 * RELAY_SERVER_PLAN.md 20, DC16_NETWORK_PROTOCOL.md 4.4 / 6.9).
 *
 * Runs inside the 1997 game: patch_online.py appends this code as the section `.dccode` of
 * `Dark Colony Ultimate.exe` and points the main menu's button id 8 (ONLINE WAR) at
 * `online_dispatch`. What it does, in order:
 *
 *   1. the Dark Colony prefix mode and the music source ALL, exactly as MULTI PLAYER WAR does;
 *   2. reads DEFAULT_SERVER.TXT beside the exe (C/C++ comments, `host[:port]`, optional `plain`);
 *   3. shows the ONLINE WAR screen (the game's own interface engine, script `intrf_hd/onlin` or
 *      `intrface/onlin`, derived from the LOAD GAME picker) with a status line;
 *   4. connects to the relay - TCP, then a TLS handshake through Windows Schannel unless `plain` -
 *      and sends 0x50 LIST; every 0x51 ROOMS fills the list, 0x53 REFUSED goes to the status line;
 *   5. ENTER sends 0x52 for the selected room; on 0x54 ENTERING it opens a loopback listener,
 *      starts a proxy thread that pipes the game's bytes through the (encrypted) relay connection
 *      and calls the stock network entry 0x40122C with 127.0.0.1:<port> - the game's own lobby and
 *      battle code run unchanged; BACK (or a lost connection + BACK) returns to the menu.
 *
 * Build: build.cmd (MSVC x86, no C run-time: /O1 /Oi- /GS- /Zl, linked as a relocatable DLL with
 * .rdata/.data/.bss merged into .text; the tool rebases .text to the section's VA). The only
 * imports taken from the exe are LoadLibraryA and GetProcAddress (their IAT slots); everything
 * else is resolved at run time, so the exe's import table is untouched.
 *
 * Calling conventions: the game is Watcom register-convention code (arguments in eax, edx, ebx,
 * ecx; esi/edi/ebp preserved by the callee; a stack argument is popped by the callee). Every game
 * function is reached through a naked thunk below; the module itself is plain cdecl C.
 */

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#define SECURITY_WIN32
#include <security.h>
#include <schannel.h>

/* ------------------------------------------------------------------ game addresses (Ultimate) */
#define GAME_LOAD_INTERFACE 0x423248   /* ip = f(ui, name, 0)                                    */
#define GAME_UNLOAD         0x423210   /* f(&ip)                                                */
#define GAME_DRAW           0x427B44   /* f(ip): paint the loaded screen                          */
#define GAME_LIST_SET       0x42A5B8   /* f(ip, list id, char** rows, count)                      */
#define GAME_LIST_SEL       0x42A828   /* f(ip, list id) -> selected index or -1                  */
#define GAME_SET_TEXT       0x423ED4   /* f(ip, in_text id, text)                                 */
#define GAME_PUMP           0x42417C   /* f(ip, &arg) -> 0 nothing, 1 button (arg = id), 7 list   */
#define GAME_POOL_MARK      0x40C0FC   /* f(pool, 0, name)   smalloc bookmark, as the picker      */
#define GAME_POOL_FREE      0x40C26C   /* f(pool, name)                                          */
#define GAME_STUB_DC_SET    0x47F340   /* fix ozi: prefix `dc/`, save folder `save`, music DC     */
#define GAME_NET_ENTRY      0x40122C   /* al = f(ui, &addr, gs, net; push flag 0): connect, lobby, battle       */
#define GAME_GET_TCP_NET    0x42E024   /* net = f(): the TCP network object (vtable +0x64 = connect 0x42DCEC); with net = 0 the entry makes the in-process mailbox network = the host's own connection */
#define GAME_MENU_LOOP_HEAD 0x40513D   /* where the id chain of the main menu continues           */
#define GAME_MUSIC_SRC      0x5327F4   /* byte: 0 DC, 1 CW, 2 ALL (fix music)                     */
#define GAME_UI_POOL_OFF    0x24       /* ui + 0x24 = the smalloc pool the screens live in         */
#define GAME_GS_CAMPAIGN    0x14F0     /* gs + 0x14F0 = 2 for a network game (MULTI PLAYER WAR)   */
#define IAT_LOADLIBRARYA    0x4804B0
#define IAT_GETPROCADDRESS  0x480480

/* widget ids of the ONLINE screen (from LOADGE; see patch_online.py online_script()) */
#define W_LIST    0
#define W_BACK    4
#define W_ENTER   5
#define W_STATUS  17   /* second line under the list: the connection state            */
#define W_HEADER  30   /* the column header above the list                            */
#define W_SERVER  31   /* first line under the list: "Server: host:port"              */

/* protocol */
#define M_LIST      0x50
#define M_ROOMS     0x51
#define M_ENTER     0x52
#define M_REFUSED   0x53
#define M_ENTERING  0x54
#define M_KEEPALIVE 0x71
#define MAX_ROOMS   7
#define ROW_CHARS   56   /* the list: 448 px / 8 px per column (MFONTO5 7 px glyphs, 1 px apart) */
#define KEEPALIVE_MS 700
#define DEFAULT_TLS_PORT   8889
#define DEFAULT_PLAIN_PORT 8888

/* ------------------------------------------------------------------ tiny run-time */
#pragma function(memset)
#pragma function(memcpy)
void* __cdecl memset(void* d, int c, size_t n) { unsigned char* p = (unsigned char*)d; while (n--) *p++ = (unsigned char)c; return d; }
void* __cdecl memcpy(void* d, const void* s, size_t n) { unsigned char* p = (unsigned char*)d; const unsigned char* q = (const unsigned char*)s; while (n--) *p++ = *q++; return d; }

static int slen(const char* s) { int n = 0; while (s[n]) n++; return n; }
static void scopy(char* d, const char* s, int cap) { int i = 0; while (s[i] && i < cap - 1) { d[i] = s[i]; i++; } d[i] = 0; }
static void scat(char* d, const char* s, int cap) { int n = slen(d); scopy(d + n, s, cap - n); }
static void scat_uint(char* d, unsigned v, int cap) { char t[12]; int i = 11; t[i] = 0; if (!v) t[--i] = '0'; while (v) { t[--i] = (char)('0' + v % 10); v /= 10; } scat(d, t + i, cap); }
static void scat_hex(char* d, unsigned v, int cap) { char t[12]; int i; t[10] = 0; for (i = 9; i >= 2; i--) { t[i] = "0123456789ABCDEF"[v & 15]; v >>= 4; } t[0] = '0'; t[1] = 'x'; scat(d, t, cap); }
static int is_space(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == '\v'; }
static int ieq(const char* a, const char* b) { for (;; a++, b++) { char x = *a, y = *b; if (x >= 'A' && x <= 'Z') x += 32; if (y >= 'A' && y <= 'Z') y += 32; if (x != y) return 0; if (!x) return 1; } }

/* ------------------------------------------------------------------ Windows functions, resolved at run time */
typedef HMODULE (WINAPI* PFN_LoadLibraryA)(LPCSTR);
typedef FARPROC (WINAPI* PFN_GetProcAddress)(HMODULE, LPCSTR);
typedef HANDLE (WINAPI* PFN_CreateFileA)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef BOOL (WINAPI* PFN_ReadFile)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef BOOL (WINAPI* PFN_CloseHandle)(HANDLE);
typedef DWORD (WINAPI* PFN_GetTickCount)(void);
typedef HANDLE (WINAPI* PFN_CreateThread)(LPSECURITY_ATTRIBUTES, SIZE_T, LPTHREAD_START_ROUTINE, LPVOID, DWORD, LPDWORD);
typedef VOID (WINAPI* PFN_Sleep)(DWORD);
typedef DWORD (WINAPI* PFN_GetLastError)(void);
typedef LPVOID (WINAPI* PFN_VirtualAlloc)(LPVOID, SIZE_T, DWORD, DWORD);
typedef BOOL (WINAPI* PFN_WriteFile)(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef DWORD (WINAPI* PFN_SetFilePointer)(HANDLE, LONG, PLONG, DWORD);

typedef int (WSAAPI* PFN_WSAStartup)(WORD, LPWSADATA);
typedef SOCKET (WSAAPI* PFN_socket)(int, int, int);
typedef int (WSAAPI* PFN_connect)(SOCKET, const struct sockaddr*, int);
typedef int (WSAAPI* PFN_send)(SOCKET, const char*, int, int);
typedef int (WSAAPI* PFN_recv)(SOCKET, char*, int, int);
typedef int (WSAAPI* PFN_select)(int, fd_set*, fd_set*, fd_set*, const struct timeval*);
typedef int (WSAAPI* PFN_closesocket)(SOCKET);
typedef struct hostent* (WSAAPI* PFN_gethostbyname)(const char*);
typedef unsigned long (WSAAPI* PFN_inet_addr)(const char*);
typedef u_short (WSAAPI* PFN_htons)(u_short);
typedef u_short (WSAAPI* PFN_ntohs)(u_short);
typedef int (WSAAPI* PFN_bind)(SOCKET, const struct sockaddr*, int);
typedef int (WSAAPI* PFN_listen)(SOCKET, int);
typedef SOCKET (WSAAPI* PFN_accept)(SOCKET, struct sockaddr*, int*);
typedef int (WSAAPI* PFN_getsockname)(SOCKET, struct sockaddr*, int*);
typedef int (WSAAPI* PFN_WSAGetLastError)(void);
typedef int (WSAAPI* PFN___WSAFDIsSet)(SOCKET, fd_set*);

typedef SECURITY_STATUS (WINAPI* PFN_AcquireCredentialsHandleA)(LPSTR, LPSTR, unsigned long, void*, void*, SEC_GET_KEY_FN, void*, PCredHandle, PTimeStamp);
typedef SECURITY_STATUS (WINAPI* PFN_InitializeSecurityContextA)(PCredHandle, PCtxtHandle, SEC_CHAR*, unsigned long, unsigned long, unsigned long, PSecBufferDesc, unsigned long, PCtxtHandle, PSecBufferDesc, unsigned long*, PTimeStamp);
typedef SECURITY_STATUS (WINAPI* PFN_QueryContextAttributesA)(PCtxtHandle, unsigned long, void*);
typedef SECURITY_STATUS (WINAPI* PFN_EncryptMessage)(PCtxtHandle, unsigned long, PSecBufferDesc, unsigned long);
typedef SECURITY_STATUS (WINAPI* PFN_DecryptMessage)(PCtxtHandle, PSecBufferDesc, unsigned long, unsigned long*);
typedef SECURITY_STATUS (WINAPI* PFN_FreeContextBuffer)(void*);
typedef SECURITY_STATUS (WINAPI* PFN_DeleteSecurityContext)(PCtxtHandle);
typedef SECURITY_STATUS (WINAPI* PFN_FreeCredentialsHandle)(PCredHandle);

static struct {
    PFN_CreateFileA CreateFileA; PFN_ReadFile ReadFile; PFN_CloseHandle CloseHandle; PFN_GetTickCount GetTickCount;
    PFN_CreateThread CreateThread; PFN_Sleep Sleep; PFN_GetLastError GetLastError; PFN_VirtualAlloc VirtualAlloc; PFN_WriteFile WriteFile; PFN_SetFilePointer SetFilePointer;
    PFN_WSAStartup WSAStartup; PFN_socket socket; PFN_connect connect; PFN_send send; PFN_recv recv; PFN_select select;
    PFN_closesocket closesocket; PFN_gethostbyname gethostbyname; PFN_inet_addr inet_addr; PFN_htons htons; PFN_ntohs ntohs;
    PFN_bind bind; PFN_listen listen; PFN_accept accept; PFN_getsockname getsockname; PFN_WSAGetLastError WSAGetLastError;
    PFN___WSAFDIsSet __WSAFDIsSet;
    PFN_AcquireCredentialsHandleA AcquireCredentialsHandleA; PFN_InitializeSecurityContextA InitializeSecurityContextA;
    PFN_QueryContextAttributesA QueryContextAttributesA; PFN_EncryptMessage EncryptMessage; PFN_DecryptMessage DecryptMessage;
    PFN_FreeContextBuffer FreeContextBuffer; PFN_DeleteSecurityContext DeleteSecurityContext; PFN_FreeCredentialsHandle FreeCredentialsHandle;
    int ready;
} W;

static FARPROC need(PFN_GetProcAddress gpa, HMODULE m, const char* name, int* ok) {
    FARPROC f = m ? gpa(m, name) : 0;
    if (!f) *ok = 0;
    return f;
}

static void logf(const char* text);

static int resolve_imports(void) {
    PFN_LoadLibraryA ll = *(PFN_LoadLibraryA*)IAT_LOADLIBRARYA;
    PFN_GetProcAddress gpa = *(PFN_GetProcAddress*)IAT_GETPROCADDRESS;
    HMODULE k32, ws, sec;
    int ok = 1;
    if (W.ready) return 1;
    k32 = ll("kernel32.dll");
    ws = ll("ws2_32.dll");
    sec = ll("secur32.dll");
    W.CreateFileA = (PFN_CreateFileA)need(gpa, k32, "CreateFileA", &ok);
    W.ReadFile = (PFN_ReadFile)need(gpa, k32, "ReadFile", &ok);
    W.CloseHandle = (PFN_CloseHandle)need(gpa, k32, "CloseHandle", &ok);
    W.GetTickCount = (PFN_GetTickCount)need(gpa, k32, "GetTickCount", &ok);
    W.CreateThread = (PFN_CreateThread)need(gpa, k32, "CreateThread", &ok);
    W.Sleep = (PFN_Sleep)need(gpa, k32, "Sleep", &ok);
    W.GetLastError = (PFN_GetLastError)need(gpa, k32, "GetLastError", &ok);
    W.VirtualAlloc = (PFN_VirtualAlloc)need(gpa, k32, "VirtualAlloc", &ok);
    W.WriteFile = (PFN_WriteFile)need(gpa, k32, "WriteFile", &ok);
    W.SetFilePointer = (PFN_SetFilePointer)need(gpa, k32, "SetFilePointer", &ok);
    W.WSAStartup = (PFN_WSAStartup)need(gpa, ws, "WSAStartup", &ok);
    W.socket = (PFN_socket)need(gpa, ws, "socket", &ok);
    W.connect = (PFN_connect)need(gpa, ws, "connect", &ok);
    W.send = (PFN_send)need(gpa, ws, "send", &ok);
    W.recv = (PFN_recv)need(gpa, ws, "recv", &ok);
    W.select = (PFN_select)need(gpa, ws, "select", &ok);
    W.closesocket = (PFN_closesocket)need(gpa, ws, "closesocket", &ok);
    W.gethostbyname = (PFN_gethostbyname)need(gpa, ws, "gethostbyname", &ok);
    W.inet_addr = (PFN_inet_addr)need(gpa, ws, "inet_addr", &ok);
    W.htons = (PFN_htons)need(gpa, ws, "htons", &ok);
    W.ntohs = (PFN_ntohs)need(gpa, ws, "ntohs", &ok);
    W.bind = (PFN_bind)need(gpa, ws, "bind", &ok);
    W.listen = (PFN_listen)need(gpa, ws, "listen", &ok);
    W.accept = (PFN_accept)need(gpa, ws, "accept", &ok);
    W.getsockname = (PFN_getsockname)need(gpa, ws, "getsockname", &ok);
    W.WSAGetLastError = (PFN_WSAGetLastError)need(gpa, ws, "WSAGetLastError", &ok);
    W.__WSAFDIsSet = (PFN___WSAFDIsSet)need(gpa, ws, "__WSAFDIsSet", &ok);
    W.AcquireCredentialsHandleA = (PFN_AcquireCredentialsHandleA)need(gpa, sec, "AcquireCredentialsHandleA", &ok);
    W.InitializeSecurityContextA = (PFN_InitializeSecurityContextA)need(gpa, sec, "InitializeSecurityContextA", &ok);
    W.QueryContextAttributesA = (PFN_QueryContextAttributesA)need(gpa, sec, "QueryContextAttributesA", &ok);
    W.EncryptMessage = (PFN_EncryptMessage)need(gpa, sec, "EncryptMessage", &ok);
    W.DecryptMessage = (PFN_DecryptMessage)need(gpa, sec, "DecryptMessage", &ok);
    W.FreeContextBuffer = (PFN_FreeContextBuffer)need(gpa, sec, "FreeContextBuffer", &ok);
    W.DeleteSecurityContext = (PFN_DeleteSecurityContext)need(gpa, sec, "DeleteSecurityContext", &ok);
    W.FreeCredentialsHandle = (PFN_FreeCredentialsHandle)need(gpa, sec, "FreeCredentialsHandle", &ok);
    W.ready = ok;
    if (!k32) return 0;
    if (!ok) {
        if (!ws) logf("resolve: ws2_32.dll not loaded");
        if (!sec) logf("resolve: secur32.dll not loaded");
        logf("resolve: a function is missing (see the W table in online.c)");
    }
    return ok;
}

static int fd_isset(SOCKET s, fd_set* set) { return W.__WSAFDIsSet(s, set); }

/* ONLINE.LOG beside the exe: one line per step, appended (the file is the first place to look when
   ONLINE WAR does nothing; it stays small). */
static void logf(const char* text) {
    HANDLE h; DWORD n; char line[200];
    if (!W.CreateFileA || !W.WriteFile) return;
    h = W.CreateFileA("ONLINE.LOG", GENERIC_WRITE, FILE_SHARE_READ, 0, OPEN_ALWAYS, 0, 0);
    if (h == INVALID_HANDLE_VALUE) return;
    W.SetFilePointer(h, 0, 0, FILE_END);
    scopy(line, text, sizeof line - 2); scat(line, "\r\n", sizeof line);
    W.WriteFile(h, line, (DWORD)slen(line), &n, 0);
    W.CloseHandle(h);
}
static void logf2(const char* a, const char* b) { char t[200]; scopy(t, a, sizeof t); scat(t, b, sizeof t); logf(t); }
static void logu(const char* a, unsigned v) { char t[200]; scopy(t, a, sizeof t); scat_uint(t, v, sizeof t); logf(t); }

/* ------------------------------------------------------------------ thunks into the game (Watcom register convention) */
static __declspec(naked) void* g_load_interface(void* ui, const char* name) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov edx, [esp+20]
            xor ebx, ebx
            mov esi, GAME_LOAD_INTERFACE
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void g_unload(void** pip) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov esi, GAME_UNLOAD
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void g_draw(void* ip) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov esi, GAME_DRAW
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void g_list_set(void* ip, int id, const char** rows, int count) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov edx, [esp+20]
            mov ebx, [esp+24]
            mov ecx, [esp+28]
            mov esi, GAME_LIST_SET
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) int g_list_sel(void* ip, int id) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov edx, [esp+20]
            mov esi, GAME_LIST_SEL
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void g_set_text(void* ip, int id, const char* text) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov edx, [esp+20]
            mov ebx, [esp+24]
            mov esi, GAME_SET_TEXT
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) int g_pump(void* ip, int* arg) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov edx, [esp+20]
            mov esi, GAME_PUMP
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void g_pool_mark(void* pool, const char* name) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            xor edx, edx
            mov ebx, [esp+20]
            mov esi, GAME_POOL_MARK
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void g_pool_free(void* pool, const char* name) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov edx, [esp+20]
            mov esi, GAME_POOL_FREE
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void g_stub_dc_set(void) {
    __asm {
            push ebx
            push esi
            push edi
            mov esi, GAME_STUB_DC_SET
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
static __declspec(naked) void* g_get_tcp_network(void) {
    __asm {
            push ebx
            push esi
            push edi
            mov esi, GAME_GET_TCP_NET
            call esi
            pop edi
            pop esi
            pop ebx
            ret
    }
}
/* al = f(eax = ui, edx = &addr, ebx = gs, ecx = the TCP net object; [esp+4] = flag, popped by the callee) */
static __declspec(naked) int g_net_entry(void* ui, void* addr, void* gs, void* net, int flag) {
    __asm {
            push ebx
            push esi
            push edi
            mov eax, [esp+16]
            mov edx, [esp+20]
            mov ebx, [esp+24]
            mov ecx, [esp+28]
            push dword ptr [esp+32]
            mov esi, GAME_NET_ENTRY
            call esi
            and eax, 0xFF
            pop edi
            pop esi
            pop ebx
            ret
    }
}

/* ------------------------------------------------------------------ the stream: plain TCP or TLS */
#define TLS_MAX_RECORD 16384
#define IN_CAP  (TLS_MAX_RECORD + 2048)
#define PLAIN_CAP (TLS_MAX_RECORD + 2048)

typedef struct {
    SOCKET s;
    int tls;
    int closed;
    CredHandle cred;
    CtxtHandle ctx;
    int have_cred, have_ctx;
    SecPkgContext_StreamSizes sizes;
    unsigned char in[IN_CAP];          /* ciphertext received, not yet decrypted            */
    int in_len;
    unsigned char plain[PLAIN_CAP];    /* plaintext decrypted, not yet consumed              */
    int plain_pos, plain_len;
    unsigned char out[TLS_MAX_RECORD + 1024];
    char err[96];
} Stream;

static Stream* g_st;          /* allocated once by online_war (see Work) */

static int sock_readable(SOCKET s, int timeout_ms) {
    fd_set rs; struct timeval tv;
    FD_ZERO(&rs); FD_SET(s, &rs);
    tv.tv_sec = timeout_ms / 1000; tv.tv_usec = (timeout_ms % 1000) * 1000;
    return W.select(0, &rs, 0, 0, &tv) > 0 && fd_isset(s, &rs);
}

static int send_all(SOCKET s, const unsigned char* p, int n) {
    while (n > 0) {
        int k = W.send(s, (const char*)p, n, 0);
        if (k <= 0) return 0;
        p += k; n -= k;
    }
    return 1;
}

static void stream_close(Stream* st) {
    if (st->s != INVALID_SOCKET) { W.closesocket(st->s); st->s = INVALID_SOCKET; }
    if (st->have_ctx) { W.DeleteSecurityContext(&st->ctx); st->have_ctx = 0; }
    if (st->have_cred) { W.FreeCredentialsHandle(&st->cred); st->have_cred = 0; }
    st->closed = 1;
}

/* Send plaintext: one TLS record per call (n <= cbMaximumMessage) or a plain send. */
static int stream_send(Stream* st, const unsigned char* p, int n) {
    if (st->closed) return 0;
    if (!st->tls) return send_all(st->s, p, n);
    while (n > 0) {
        int chunk = n; SecBuffer b[4]; SecBufferDesc d; SECURITY_STATUS ss;
        if (chunk > (int)st->sizes.cbMaximumMessage) chunk = (int)st->sizes.cbMaximumMessage;
        memcpy(st->out + st->sizes.cbHeader, p, chunk);
        b[0].BufferType = SECBUFFER_STREAM_HEADER; b[0].pvBuffer = st->out; b[0].cbBuffer = st->sizes.cbHeader;
        b[1].BufferType = SECBUFFER_DATA; b[1].pvBuffer = st->out + st->sizes.cbHeader; b[1].cbBuffer = chunk;
        b[2].BufferType = SECBUFFER_STREAM_TRAILER; b[2].pvBuffer = st->out + st->sizes.cbHeader + chunk; b[2].cbBuffer = st->sizes.cbTrailer;
        b[3].BufferType = SECBUFFER_EMPTY; b[3].pvBuffer = 0; b[3].cbBuffer = 0;
        d.ulVersion = SECBUFFER_VERSION; d.cBuffers = 4; d.pBuffers = b;
        ss = W.EncryptMessage(&st->ctx, 0, &d, 0);
        if (ss != SEC_E_OK) { scopy(st->err, "TLS encrypt failed ", sizeof st->err); scat_hex(st->err, (unsigned)ss, sizeof st->err); return 0; }
        if (!send_all(st->s, st->out, (int)(b[0].cbBuffer + b[1].cbBuffer + b[2].cbBuffer))) return 0;
        p += chunk; n -= chunk;
    }
    return 1;
}

/* Decrypt what is in `in`; returns 1 if plaintext was produced, 0 if more ciphertext is needed, -1 on error/close. */
static int tls_decrypt_pending(Stream* st) {
    while (st->in_len > 0) {
        SecBuffer b[4]; SecBufferDesc d; SECURITY_STATUS ss; int i, produced = 0;
        b[0].BufferType = SECBUFFER_DATA; b[0].pvBuffer = st->in; b[0].cbBuffer = st->in_len;
        for (i = 1; i < 4; i++) { b[i].BufferType = SECBUFFER_EMPTY; b[i].pvBuffer = 0; b[i].cbBuffer = 0; }
        d.ulVersion = SECBUFFER_VERSION; d.cBuffers = 4; d.pBuffers = b;
        ss = W.DecryptMessage(&st->ctx, &d, 0, 0);
        if (ss == SEC_E_INCOMPLETE_MESSAGE) return 0;
        if (ss == SEC_I_CONTEXT_EXPIRED) { st->closed = 1; return -1; }
        if (ss != SEC_E_OK && ss != SEC_I_RENEGOTIATE) { scopy(st->err, "TLS decrypt failed ", sizeof st->err); scat_hex(st->err, (unsigned)ss, sizeof st->err); return -1; }
        {
            int extra = 0; const unsigned char* extra_p = 0;
            for (i = 0; i < 4; i++) {
                if (b[i].BufferType == SECBUFFER_DATA && b[i].cbBuffer) {
                    int room = PLAIN_CAP - st->plain_len;
                    int n = (int)b[i].cbBuffer; if (n > room) n = room;   /* the caller drains plain before the next call */
                    memcpy(st->plain + st->plain_len, b[i].pvBuffer, n); st->plain_len += n; produced = 1;
                }
                if (b[i].BufferType == SECBUFFER_EXTRA) { extra = (int)b[i].cbBuffer; extra_p = (const unsigned char*)b[i].pvBuffer; }
            }
            if (extra) { memcpy(st->in, extra_p, extra); st->in_len = extra; } else st->in_len = 0;
        }
        if (ss == SEC_I_RENEGOTIATE) { scopy(st->err, "TLS renegotiation requested", sizeof st->err); return -1; }
        if (produced) return 1;
    }
    return 0;
}

/* Non-blocking read of plaintext into buf (up to cap). Returns bytes (>0), 0 = nothing yet, -1 = closed/error. */
static int stream_recv(Stream* st, unsigned char* buf, int cap, int wait_ms) {
    if (st->closed) return -1;
    for (;;) {
        if (st->tls && st->plain_len > st->plain_pos) {
            int n = st->plain_len - st->plain_pos; if (n > cap) n = cap;
            memcpy(buf, st->plain + st->plain_pos, n); st->plain_pos += n;
            if (st->plain_pos == st->plain_len) st->plain_pos = st->plain_len = 0;
            return n;
        }
        if (st->tls) {
            int r = tls_decrypt_pending(st);
            if (r < 0) return -1;
            if (r > 0) continue;
        }
        if (!sock_readable(st->s, wait_ms)) return 0;
        if (st->tls) {
            int k = W.recv(st->s, (char*)st->in + st->in_len, IN_CAP - st->in_len, 0);
            if (k <= 0) { st->closed = 1; return -1; }
            st->in_len += k;
            wait_ms = 0;
            continue;
        } else {
            int k = W.recv(st->s, (char*)buf, cap, 0);
            if (k <= 0) { st->closed = 1; return -1; }
            return k;
        }
    }
}

/* The TLS client handshake (Schannel), blocking; the host name is the certificate's subject to check. */
static int tls_handshake(Stream* st, char* host) {
    SCHANNEL_CRED sc; SECURITY_STATUS ss; unsigned long attrs; unsigned long flags;
    SecBuffer ob[1]; SecBufferDesc od; SecBuffer ib[2]; SecBufferDesc id;
    int first = 1;
    memset(&sc, 0, sizeof sc);
    sc.dwVersion = SCHANNEL_CRED_VERSION;
    sc.grbitEnabledProtocols = 0;   /* the system's defaults (TLS 1.2 and, on current Windows, 1.3) */
    sc.dwFlags = SCH_CRED_AUTO_CRED_VALIDATION | SCH_CRED_NO_DEFAULT_CREDS | SCH_USE_STRONG_CRYPTO;
    ss = W.AcquireCredentialsHandleA(0, UNISP_NAME_A, SECPKG_CRED_OUTBOUND, 0, &sc, 0, 0, &st->cred, 0);
    if (ss != SEC_E_OK) { scopy(st->err, "TLS credentials failed ", sizeof st->err); scat_hex(st->err, (unsigned)ss, sizeof st->err); return 0; }
    st->have_cred = 1;
    flags = ISC_REQ_SEQUENCE_DETECT | ISC_REQ_REPLAY_DETECT | ISC_REQ_CONFIDENTIALITY | ISC_REQ_ALLOCATE_MEMORY | ISC_REQ_STREAM;
    st->in_len = 0;
    for (;;) {
        ob[0].BufferType = SECBUFFER_TOKEN; ob[0].pvBuffer = 0; ob[0].cbBuffer = 0;
        od.ulVersion = SECBUFFER_VERSION; od.cBuffers = 1; od.pBuffers = ob;
        if (first) {
            ss = W.InitializeSecurityContextA(&st->cred, 0, host, flags, 0, 0, 0, 0, &st->ctx, &od, &attrs, 0);
            first = 0; st->have_ctx = 1;
        } else {
            if (st->in_len == 0 || ss == SEC_E_INCOMPLETE_MESSAGE) {
                int k;
                if (!sock_readable(st->s, 15000)) { scopy(st->err, "TLS handshake timed out", sizeof st->err); return 0; }
                k = W.recv(st->s, (char*)st->in + st->in_len, IN_CAP - st->in_len, 0);
                if (k <= 0) { scopy(st->err, "connection closed during the TLS handshake", sizeof st->err); return 0; }
                st->in_len += k;
            }
            ib[0].BufferType = SECBUFFER_TOKEN; ib[0].pvBuffer = st->in; ib[0].cbBuffer = st->in_len;
            ib[1].BufferType = SECBUFFER_EMPTY; ib[1].pvBuffer = 0; ib[1].cbBuffer = 0;
            id.ulVersion = SECBUFFER_VERSION; id.cBuffers = 2; id.pBuffers = ib;
            ss = W.InitializeSecurityContextA(&st->cred, &st->ctx, host, flags, 0, 0, &id, 0, 0, &od, &attrs, 0);
            if (ss == SEC_E_INCOMPLETE_MESSAGE) continue;        /* keep what we have, read more */
            if (ib[1].BufferType == SECBUFFER_EXTRA && ib[1].cbBuffer) {
                /* the extra bytes are the tail of `in`: keep them for the next round / the data phase */
                int extra = (int)ib[1].cbBuffer;
                memcpy(st->in, st->in + st->in_len - extra, extra);
                st->in_len = extra;
            } else {
                st->in_len = 0;
            }
        }
        if (ob[0].pvBuffer && ob[0].cbBuffer) {
            int okk = send_all(st->s, (const unsigned char*)ob[0].pvBuffer, (int)ob[0].cbBuffer);
            W.FreeContextBuffer(ob[0].pvBuffer);
            if (!okk) { scopy(st->err, "send failed during the TLS handshake", sizeof st->err); return 0; }
        }
        if (ss == SEC_E_OK) break;
        if (ss == SEC_I_CONTINUE_NEEDED) continue;
        scopy(st->err, "TLS handshake failed ", sizeof st->err); scat_hex(st->err, (unsigned)ss, sizeof st->err);
        if ((unsigned)ss == 0x80090325) scat(st->err, " (certificate not trusted)", sizeof st->err);
        else if ((unsigned)ss == 0x80090322) scat(st->err, " (certificate name mismatch)", sizeof st->err);
        else if ((unsigned)ss == 0x80090326) scat(st->err, " (not a TLS server; try `plain`)", sizeof st->err);
        return 0;
    }
    ss = W.QueryContextAttributesA(&st->ctx, SECPKG_ATTR_STREAM_SIZES, &st->sizes);
    if (ss != SEC_E_OK) { scopy(st->err, "TLS stream sizes failed", sizeof st->err); return 0; }
    st->tls = 1;
    return 1;
}

/* ------------------------------------------------------------------ DEFAULT_SERVER.TXT */
typedef struct { char host[128]; unsigned short port; int plain; } ServerConfig;

static int read_file(const char* name, char* buf, int cap) {
    HANDLE h; DWORD got = 0;
    h = W.CreateFileA(name, GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, 0, 0);
    if (h == INVALID_HANDLE_VALUE) return -1;
    if (!W.ReadFile(h, buf, (DWORD)(cap - 1), &got, 0)) got = 0;
    W.CloseHandle(h);
    buf[got] = 0;
    return (int)got;
}

static int file_exists(const char* name) {
    HANDLE h = W.CreateFileA(name, GENERIC_READ, FILE_SHARE_READ, 0, OPEN_EXISTING, 0, 0);
    if (h == INVALID_HANDLE_VALUE) return 0;
    W.CloseHandle(h);
    return 1;
}

/* Blank out the two C++ comment forms (line and block) in place, keeping line breaks. */
static void strip_comments(char* s) {
    int i = 0;
    while (s[i]) {
        if (s[i] == '/' && s[i + 1] == '/') { while (s[i] && s[i] != '\n') s[i++] = ' '; }
        else if (s[i] == '/' && s[i + 1] == '*') {
            s[i++] = ' '; s[i++] = ' ';
            while (s[i] && !(s[i] == '*' && s[i + 1] == '/')) { if (s[i] != '\n') s[i] = ' '; i++; }
            if (s[i]) { s[i++] = ' '; s[i++] = ' '; }
        } else i++;
    }
}

static char* g_cfgbuf;        /* 4096 bytes in Work */
#define CFGBUF_CAP 4096

/* Returns 0 ok, 1 file missing, 2 no address in it. */
static int read_config(ServerConfig* c, char* err, int errcap) {
    char* p; int n, tok = 0;
    memset(c, 0, sizeof *c);
    n = read_file("DEFAULT_SERVER.TXT", g_cfgbuf, CFGBUF_CAP);
    if (n < 0) { scopy(err, "DEFAULT_SERVER.TXT not found beside the game", errcap); return 1; }
    strip_comments(g_cfgbuf);
    p = g_cfgbuf;
    while (*p) {
        char word[160]; int w = 0;
        while (*p && is_space(*p)) p++;
        if (!*p) break;
        while (*p && !is_space(*p) && w < (int)sizeof word - 1) word[w++] = *p++;
        word[w] = 0;
        while (*p && !is_space(*p)) p++;
        if (tok == 0) {
            int i, colon = -1;
            for (i = 0; word[i]; i++) if (word[i] == ':') colon = i;
            if (colon >= 0) {
                unsigned v = 0; int k;
                for (k = colon + 1; word[k]; k++) { if (word[k] < '0' || word[k] > '9') { v = 0; break; } v = v * 10 + (unsigned)(word[k] - '0'); }
                if (v > 0 && v < 65536) c->port = (unsigned short)v;
                word[colon] = 0;
            }
            scopy(c->host, word, sizeof c->host);
        } else if (ieq(word, "plain") || ieq(word, "notls")) {
            c->plain = 1;
        }
        tok++;
    }
    if (!c->host[0]) { scopy(err, "DEFAULT_SERVER.TXT names no server address", errcap); return 2; }
    if (!c->port) c->port = c->plain ? DEFAULT_PLAIN_PORT : DEFAULT_TLS_PORT;
    return 0;
}

/* ------------------------------------------------------------------ frames */
static unsigned char g_seq;
static unsigned char* g_frame;  /* 1030 bytes in Work */

static int send_command(Stream* st, const unsigned char* cmd, int n) {
    int len = n + 3;
    g_frame[0] = (unsigned char)(len & 0xFF);
    g_frame[1] = (unsigned char)(((len >> 8) & 0x0F) | (g_seq << 4));
    memcpy(g_frame + 2, cmd, n);
    g_frame[len - 1] = 0;
    g_seq = (unsigned char)((g_seq + 1) & 15);
    return stream_send(st, g_frame, len);
}

/* ------------------------------------------------------------------ the room table */
typedef struct { unsigned char id, state, seats, players, bots; char row[ROW_CHARS + 1]; } RoomEntry;
static RoomEntry g_rooms[MAX_ROOMS];
static const char* g_rowptr[MAX_ROOMS];
static int g_room_count;
static char g_status[128];
static unsigned char* g_rx;     /* RX_CAP bytes in Work */
#define RX_CAP 8192
static int g_rx_len;

static const char HEADER_TEXT[] = "MAP                TERRAIN  SEATS PLAYERS BOTS STATUS";

/* One 0x51 ROOMS command (after the type byte). Returns 1 if the table changed. */
static int parse_rooms(const unsigned char* p, int n) {
    int count, i, pos = 1, k;
    if (n < 1) return 0;
    count = p[0];
    if (count > MAX_ROOMS) count = MAX_ROOMS;
    for (i = 0; i < count; i++) {
        RoomEntry* r = &g_rooms[i];
        if (pos + 5 > n) return 0;
        r->id = p[pos]; r->state = p[pos + 1]; r->seats = p[pos + 2]; r->players = p[pos + 3]; r->bots = p[pos + 4];
        pos += 5;
        for (k = 0; k < 3; k++) {
            int start = pos;
            while (pos < n && p[pos]) pos++;
            if (pos >= n) return 0;
            if (k == 2) {
                int len = pos - start; if (len > ROW_CHARS) len = ROW_CHARS;
                memcpy(r->row, p + start, len); r->row[len] = 0;
            }
            pos++;
        }
        g_rowptr[i] = r->row;
    }
    g_room_count = count;
    return 1;
}

/* Handle one complete frame payload (without header/terminator). Returns 0 nothing special, 1 ROOMS, 2 REFUSED, 3 ENTERING (slot in *slot). */
static int handle_frame(const unsigned char* p, int n, int* slot) {
    if (n < 1) return 0;
    switch (p[0]) {
    case M_ROOMS:
        return parse_rooms(p + 1, n - 1) ? 1 : 0;
    case M_REFUSED: {
        int len = n - 1; if (len > (int)sizeof g_status - 1) len = (int)sizeof g_status - 1;
        memcpy(g_status, p + 1, len); g_status[len] = 0;
        { int i; for (i = 0; i < len; i++) if (!g_status[i]) { g_status[i] = 0; break; } }
        return 2;
    }
    case M_ENTERING:
        if (n >= 2) { *slot = p[1]; return 3; }
        return 0;
    default:
        return 0;   /* the hall's lobby dump, chat repaints: not for us */
    }
}

/*
 * Pull whatever the relay sent into g_rx and dispatch complete frames. Returns the highest event seen
 * (3 ENTERING stops the parsing: the bytes after that frame belong to the game), -1 on a lost connection.
 */
static int poll_relay(Stream* st, int* slot) {
    int best = 0;
    for (;;) {
        int k;
        /* complete frames first */
        while (g_rx_len >= 2) {
            int flen = g_rx[0] | ((g_rx[1] & 0x0F) << 8);
            int ev;
            if (flen < 3 || flen > 1024) { scopy(g_status, "Protocol error: bad frame from the relay", sizeof g_status); return -1; }
            if (g_rx_len < flen) break;
            ev = handle_frame(g_rx + 2, flen - 3, slot);
            memcpy(g_rx, g_rx + flen, g_rx_len - flen);
            g_rx_len -= flen;
            if (ev > best) best = ev;
            if (ev == 3) return 3;
        }
        if (g_rx_len >= RX_CAP) { scopy(g_status, "Protocol error: frame too long", sizeof g_status); return -1; }
        k = stream_recv(st, g_rx + g_rx_len, RX_CAP - g_rx_len, 0);
        if (k < 0) return -1;
        if (k == 0) return best;
        g_rx_len += k;
    }
}

/* ------------------------------------------------------------------ the loopback proxy */
static SOCKET g_listen = INVALID_SOCKET;
static unsigned char* g_pbuf;   /* PBUF_CAP bytes in Work */
#define PBUF_CAP 8192

static DWORD WINAPI proxy_thread(void* arg) {
    Stream* st = (Stream*)arg;
    SOCKET g;
    g = W.accept(g_listen, 0, 0);
    if (g_listen != INVALID_SOCKET) { W.closesocket(g_listen); g_listen = INVALID_SOCKET; }
    if (g == INVALID_SOCKET) { logf("proxy: accept failed"); stream_close(st); return 0; }
    logf("proxy: the game connected");
    /* plaintext that arrived together with ENTERING belongs to the game */
    if (g_rx_len > 0) { if (!send_all(g, g_rx, g_rx_len)) goto done; g_rx_len = 0; }
    for (;;) {
        fd_set rs; struct timeval tv; int r;
        /* decrypted-but-unread plaintext first (it does not show on the socket) */
        if (st->tls && (st->plain_len > st->plain_pos || st->in_len > 0)) {
            int n = stream_recv(st, g_pbuf, PBUF_CAP, 0);
            if (n < 0) break;
            if (n > 0) { if (!send_all(g, g_pbuf, n)) break; continue; }
        }
        FD_ZERO(&rs); FD_SET(g, &rs); FD_SET(st->s, &rs);
        tv.tv_sec = 1; tv.tv_usec = 0;
        r = W.select(0, &rs, 0, 0, &tv);
        if (r < 0) break;
        if (r == 0) continue;
        if (fd_isset(g, &rs)) {
            int n = W.recv(g, (char*)g_pbuf, PBUF_CAP, 0);
            if (n <= 0) break;                        /* the game closed its connection */
            if (!stream_send(st, g_pbuf, n)) break;
        }
        if (fd_isset(st->s, &rs)) {
            int n = stream_recv(st, g_pbuf, PBUF_CAP, 0);
            if (n < 0) break;                         /* the relay closed */
            if (n > 0 && !send_all(g, g_pbuf, n)) break;
        }
    }
done:
    logf("proxy: closing both connections");
    W.closesocket(g);
    stream_close(st);
    return 0;
}

/* ------------------------------------------------------------------ the connection */
static int connect_relay(Stream* st, ServerConfig* c) {
    WSADATA wd; SOCKET s; struct sockaddr_in sa; unsigned long ip;
    memset(st, 0, sizeof *st);
    st->s = INVALID_SOCKET;
    if (W.WSAStartup(0x0202, &wd) != 0) { scopy(st->err, "WSAStartup failed", sizeof st->err); st->closed = 1; return 0; }
    ip = W.inet_addr(c->host);
    if (ip == INADDR_NONE) {
        struct hostent* he = W.gethostbyname(c->host);
        if (!he || !he->h_addr_list || !he->h_addr_list[0]) { scopy(st->err, "Cannot resolve ", sizeof st->err); scat(st->err, c->host, sizeof st->err); st->closed = 1; return 0; }
        memcpy(&ip, he->h_addr_list[0], 4);
    }
    s = W.socket(AF_INET, SOCK_STREAM, 0);
    if (s == INVALID_SOCKET) { scopy(st->err, "socket() failed", sizeof st->err); st->closed = 1; return 0; }
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_port = W.htons(c->port); sa.sin_addr.s_addr = ip;
    if (W.connect(s, (struct sockaddr*)&sa, sizeof sa) != 0) {
        scopy(st->err, "Cannot connect to ", sizeof st->err); scat(st->err, c->host, sizeof st->err); scat(st->err, ":", sizeof st->err); scat_uint(st->err, c->port, sizeof st->err);
        scat(st->err, " (error ", sizeof st->err); scat_uint(st->err, (unsigned)W.WSAGetLastError(), sizeof st->err); scat(st->err, ")", sizeof st->err);
        W.closesocket(s); st->closed = 1; return 0;
    }
    st->s = s;
    if (!c->plain && !tls_handshake(st, c->host)) { stream_close(st); return 0; }
    return 1;
}

static int open_loopback(unsigned short* port) {
    struct sockaddr_in sa; int len = sizeof sa;
    g_listen = W.socket(AF_INET, SOCK_STREAM, 0);
    if (g_listen == INVALID_SOCKET) return 0;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_addr.s_addr = 0x0100007F; /* 127.0.0.1 */ sa.sin_port = 0;
    if (W.bind(g_listen, (struct sockaddr*)&sa, sizeof sa) != 0 || W.listen(g_listen, 1) != 0 ||
        W.getsockname(g_listen, (struct sockaddr*)&sa, &len) != 0) {
        W.closesocket(g_listen); g_listen = INVALID_SOCKET; return 0;
    }
    *port = W.ntohs(sa.sin_port);
    return 1;
}

/* ------------------------------------------------------------------ the screen */
static const char SCRIPT_HD[] = "intrf_hd/onlin";
static const char SCRIPT_STOCK[] = "intrface/onlin";
static const char PROBE_HD[] = "intrf_hd\\ONLINE";
static const char POOL_NAME[] = "BMOnline";
static const char LOOPBACK[] = "127.0.0.1";

static void status(void* ip, const char* text) {
    scopy(g_status, text, sizeof g_status);
    g_set_text(ip, W_STATUS, g_status);
}

/* Returns: 0 = BACK / failure (back to the menu), 1 = ENTERING (slot in *slot, connection kept). */
static int room_screen(void* ui, ServerConfig* c, int cfg_err, const char* cfg_msg, int* slot) {
    void* pool = *(void**)((unsigned char*)ui + GAME_UI_POOL_OFF);
    void* ip;
    int result = 0, connected = 0, lost = 0, arg = 0, entering = 0;
    DWORD last_keepalive = 0;
    const char* empty[1];
    empty[0] = "";
    g_room_count = 0; g_rx_len = 0; g_seq = 0;
    g_pool_mark(pool, POOL_NAME);
    logf2("screen: ", file_exists(PROBE_HD) ? SCRIPT_HD : SCRIPT_STOCK);
    ip = g_load_interface(ui, file_exists(PROBE_HD) ? SCRIPT_HD : SCRIPT_STOCK);
    g_draw(ip);
    logf("screen loaded");
    g_set_text(ip, W_HEADER, HEADER_TEXT);
    g_list_set(ip, W_LIST, empty, 0);
    if (cfg_err) {
        g_set_text(ip, W_SERVER, "Server: none (see DEFAULT_SERVER.TXT)");
        status(ip, cfg_msg);
        lost = 1;
    } else {
        char server[160];
        scopy(server, "Server: ", sizeof server); scat(server, c->host, sizeof server); scat(server, ":", sizeof server); scat_uint(server, c->port, sizeof server);
        g_set_text(ip, W_SERVER, server);
        scopy(g_status, c->plain ? "Connecting (no encryption)..." : "Connecting (TLS)...", sizeof g_status);
        g_set_text(ip, W_STATUS, g_status);
        g_pump(ip, &arg);   /* paint the status before the blocking connect */
        if (connect_relay(g_st, c)) {
            unsigned char cmd = M_LIST;
            connected = 1;
            logf(c->plain ? "connected (plain)" : "connected (TLS)");
            if (send_command(g_st, &cmd, 1)) status(ip, c->plain ? "Connected. Select a room and press ENTER." : "Connected (TLS). Select a room and press ENTER.");
            else { status(ip, "Connection lost."); lost = 1; }
            last_keepalive = W.GetTickCount();
        } else {
            status(ip, g_st->err);
            logf2("connect: ", g_st->err);
            lost = 1;
        }
    }
    for (;;) {
        int kind;
        if (connected && !lost) {
            int ev = poll_relay(g_st, slot);
            if (ev < 0) {
                if (!g_status[0] || g_status[0] == 'C') scopy(g_status, "Connection lost.", sizeof g_status);
                g_set_text(ip, W_STATUS, g_status); lost = 1; stream_close(g_st);
            } else if (ev == 3) { result = 1; break; }
            else if (ev == 1) { g_list_set(ip, W_LIST, g_rowptr, g_room_count); }
            else if (ev == 2) { g_set_text(ip, W_STATUS, g_status); entering = 0; }
            /* Nothing goes out between ENTER and ENTERING: the relay restarts its sequence counter for the
               game's own stream the moment it seats us, so a keep-alive still in flight would be read as the
               game's first frame and get the connection dropped ("sequence N, expected 0"; plan F81). */
            if (!lost && !entering && (DWORD)(W.GetTickCount() - last_keepalive) >= KEEPALIVE_MS) {
                unsigned char q = M_KEEPALIVE;
                last_keepalive = W.GetTickCount();
                if (!send_command(g_st, &q, 1)) { status(ip, "Connection lost."); lost = 1; stream_close(g_st); }
            }
        }
        kind = g_pump(ip, &arg);
        if (kind == 1) {
            if (arg == W_BACK) break;
            if (arg == W_ENTER) {
                int sel = g_list_sel(ip, W_LIST);
                if (lost || !connected) status(ip, cfg_err ? cfg_msg : "Not connected. Press BACK and try again.");
                else if (entering) { /* the answer is on its way; a second ENTER would be a straggler too */ }
                else if (sel < 0 || sel >= g_room_count) status(ip, "Select a room first.");
                else {
                    unsigned char cmd[2];
                    cmd[0] = M_ENTER; cmd[1] = g_rooms[sel].id;
                    scopy(g_status, "Entering room ", sizeof g_status); scat_uint(g_status, g_rooms[sel].id, sizeof g_status); scat(g_status, "...", sizeof g_status);
                    g_set_text(ip, W_STATUS, g_status);
                    if (!send_command(g_st, cmd, 2)) { status(ip, "Connection lost."); lost = 1; stream_close(g_st); }
                    else entering = 1;
                }
            }
        } else if (kind == 0) {
            W.Sleep(1);
        }
    }
    g_unload(&ip);
    g_pool_free(pool, POOL_NAME);
    if (!result && connected && !lost) stream_close(g_st);
    return result;
}

/* ------------------------------------------------------------------ entry points */
#pragma pack(push, 1)
typedef struct { unsigned short port; unsigned short pad; const char* host; } NetAddress;   /* the game reads the host pointer at +4 (0x405BEB..0x405C16) */
#pragma pack(pop)

typedef struct { Stream stream; unsigned char rx[RX_CAP]; unsigned char pbuf[PBUF_CAP]; unsigned char frame[1030]; char cfgbuf[CFGBUF_CAP]; } Work;
static Work* g_work;

__declspec(dllexport) int __cdecl online_war(void* ui, void* gs) {
    ServerConfig cfg; char msg[96]; int cfg_err, slot = -1;
    NetAddress addr; unsigned short port = 0; HANDLE th; DWORD tid;
    if (!resolve_imports()) return 0;
    logf("--- ONLINE WAR pressed");
    if (!g_work) {
        g_work = (Work*)W.VirtualAlloc(0, sizeof(Work), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        if (!g_work) { logf("VirtualAlloc failed"); return 0; }
        g_st = &g_work->stream; g_rx = g_work->rx; g_pbuf = g_work->pbuf; g_frame = g_work->frame; g_cfgbuf = g_work->cfgbuf;
    }
    /* the same mode MULTI PLAYER WAR sets: Classic tables through the dc/ prefix, music source ALL */
    g_stub_dc_set();
    *(unsigned char*)GAME_MUSIC_SRC = 2;
    *(unsigned long*)((unsigned char*)gs + GAME_GS_CAMPAIGN) = 2;
    msg[0] = 0;
    cfg_err = read_config(&cfg, msg, sizeof msg);
    if (cfg_err) logf2("config: ", msg); else { logf2("config: host ", cfg.host); logu("config: port ", cfg.port); logu("config: plain ", (unsigned)cfg.plain); }
    if (!room_screen(ui, &cfg, cfg_err, msg, &slot)) { logf("back to the menu"); return 0; }
    logu("ENTERING slot ", (unsigned)slot);
    /* ENTERING: the relay now speaks the stock protocol to whoever reads this connection */
    if (!open_loopback(&port)) { logf("loopback listener failed"); stream_close(g_st); return 0; }
    logu("loopback port ", port);
    th = W.CreateThread(0, 0, proxy_thread, g_st, 0, &tid);
    if (!th) { W.closesocket(g_listen); g_listen = INVALID_SOCKET; stream_close(g_st); return 0; }
    W.CloseHandle(th);
    addr.port = port; addr.pad = 0; addr.host = LOOPBACK;
    {
        /* the stock CONNECT handler passes the TCP network object (0x405BAF get_tcp_network -> ecx); with 0 the
           entry would build the mailbox network of the in-game host and connect the game to itself */
        void* net = g_get_tcp_network();
        logu("calling the game's network entry, tcp net object ", (unsigned)net);
        logu("network entry returned ", (unsigned)g_net_entry(ui, &addr, gs, net, 0));
    }
    /* back from the lobby / battle: the game closed its socket, the proxy thread is ending; if the game
       never connected, closing the listener unblocks the thread's accept() */
    if (g_listen != INVALID_SOCKET) { W.closesocket(g_listen); g_listen = INVALID_SOCKET; }
    return 1;
}

/* The main menu's id chain jumps here from the seven NOP bytes at 0x405136 (fix ozi's tail) with
   eax = the game state, edi = the button id and [ebp-4] = the screen (see patch_ozi_menu.py). */
__declspec(dllexport) __declspec(naked) void online_dispatch(void) {
    __asm {
        cmp edi, 8
        jne back
        mov edx, [ebp-4]
        push eax            ; gs
        push edx            ; ui
        call online_war
        add esp, 8
    back:
        mov ecx, GAME_MENU_LOOP_HEAD
        jmp ecx
    }
}
