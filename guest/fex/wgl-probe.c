/* Windows WGL frontend for the same CinterForgeOS OpenGL probe. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <GL/gl.h>
#include <stdio.h>
#include <string.h>
#include "gl-probe-render.h"
#ifndef WGL_CONTEXT_MAJOR_VERSION_ARB
#define WGL_CONTEXT_MAJOR_VERSION_ARB 0x2091
#define WGL_CONTEXT_MINOR_VERSION_ARB 0x2092
#define WGL_CONTEXT_PROFILE_MASK_ARB 0x9126
#define WGL_CONTEXT_CORE_PROFILE_BIT_ARB 0x00000001
#define WGL_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB 0x00000002
#endif
typedef HGLRC (WINAPI *CreateAttribs)(HDC, HGLRC, const int *);
static LRESULT CALLBACK wndproc(HWND w, UINT m, WPARAM wp, LPARAM lp) {
    return m == WM_DESTROY ? (PostQuitMessage(0), 0) : DefWindowProcA(w,m,wp,lp);
}
int main(int argc, char **argv) {
    int rc = 0, core = 0, requested = 0;
    if (argc > 1) {
        if (!strcmp(argv[1], "core43")) core = requested = 1;
        else if (!strcmp(argv[1], "compat43")) requested = 1;
        else if (strcmp(argv[1], "legacy")) {
            fprintf(stderr,"Usage: %s [legacy|core43|compat43]\n",argv[0]); return 64;
        }
    }
    /* Proton may not forward a Windows console application's stdout to its
     * Unix caller. Write the authoritative result into this probe's own
     * Windows C: drive, which the host wrapper reads through the prefix. */
    if (!freopen("C:\\cinterforge-wgl-probe-result.log", "w", stdout)) {
        fprintf(stderr, "[wgl-probe] cannot open result log on C: drive\n");
        return 65;
    }
    setbuf(stdout, NULL);
    printf("[wgl-probe] mode=%s\n",requested?(core?"core43":"compat43"):"legacy");
    HINSTANCE instance = GetModuleHandleA(NULL);
    WNDCLASSA wc = {0}; wc.lpfnWndProc=wndproc; wc.hInstance=instance; wc.lpszClassName="CinterForgeWGLProbe";
    if (!RegisterClassA(&wc)) { printf("[wgl-probe] RegisterClass failed=%lu\n",GetLastError()); return 2; }
    HWND window = CreateWindowA(wc.lpszClassName,"CinterForge GL Probe",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                                CW_USEDEFAULT,CW_USEDEFAULT,300,300,NULL,NULL,instance,NULL);
    if (!window) { printf("[wgl-probe] CreateWindow failed=%lu\n",GetLastError()); return 3; }
    HDC dc = GetDC(window);
    PIXELFORMATDESCRIPTOR pfd = {0}; pfd.nSize=sizeof(pfd); pfd.nVersion=1;
    pfd.dwFlags=PFD_DRAW_TO_WINDOW|PFD_SUPPORT_OPENGL|PFD_DOUBLEBUFFER;
    pfd.iPixelType=PFD_TYPE_RGBA; pfd.cColorBits=24; pfd.cDepthBits=24;
    int format = ChoosePixelFormat(dc,&pfd);
    printf("[wgl-probe] pixel_format=%d\n",format);
    if (!format || !SetPixelFormat(dc,format,&pfd)) { rc=4; goto done; }
    HGLRC legacy = wglCreateContext(dc), ctx = NULL;
    printf("[wgl-probe] legacy_context=%p error=%lu\n",(void*)legacy,GetLastError());
    if (!legacy || !wglMakeCurrent(dc,legacy)) { rc=5; goto done; }
    ctx = legacy;
    if (requested) {
        CreateAttribs create = (CreateAttribs)wglGetProcAddress("wglCreateContextAttribsARB");
        printf("[wgl-probe] create_attribs=%p\n",(void*)create);
        if (!create) { rc=6; goto done; }
        const int attrs[]={WGL_CONTEXT_MAJOR_VERSION_ARB,4,WGL_CONTEXT_MINOR_VERSION_ARB,3,
          WGL_CONTEXT_PROFILE_MASK_ARB,core?WGL_CONTEXT_CORE_PROFILE_BIT_ARB:WGL_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB,0};
        wglMakeCurrent(NULL,NULL);
        ctx=create(dc,NULL,attrs);
        printf("[wgl-probe] context43=%p error=%lu\n",(void*)ctx,GetLastError());
        if (!ctx || !wglMakeCurrent(dc,ctx)) { rc=7; goto done; }
    }
    rc=cf_gl_render_check("wgl");
    if (!rc) {
        BOOL swapped=SwapBuffers(dc);
        printf("[wgl-probe] swap_buffers=%d error=%lu\n",(int)swapped,GetLastError());
        if (!swapped) rc=8;
        else Sleep(2000);
    }
done:
    wglMakeCurrent(NULL,NULL);
    if (ctx && ctx!=legacy) wglDeleteContext(ctx);
    if (legacy) wglDeleteContext(legacy);
    ReleaseDC(window,dc); DestroyWindow(window);
    printf("[wgl-probe] exit=%d\n",rc);
    return rc;
}
