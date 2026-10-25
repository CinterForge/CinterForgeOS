/* CinterForgeOS AMD64 FEX/GLX graphics probe.
 * Exercises real GLX context activation, OpenGL calls and buffer swapping
 * through FEX GL thunks. No benchmark-specific workarounds. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <GL/gl.h>
#include <GL/glx.h>
#include <GL/glxext.h>

#ifndef CINTERFORGE_GLX_PROBE_SOURCE_SHA
#define CINTERFORGE_GLX_PROBE_SOURCE_SHA "unversioned"
#endif

static volatile int xerrors;
static int on_x_error(Display *dpy, XErrorEvent *e) {
    char message[256] = {0};
    XGetErrorText(dpy, e->error_code, message, sizeof(message));
    fprintf(stderr, "[glx-probe] XError code=%u (%s) request=%u minor=%u serial=%lu resource=0x%lx\n",
            e->error_code, message, e->request_code, e->minor_code,
            e->serial, e->resourceid);
    ++xerrors;
    return 0;
}

static int checked(Display *d, const char *step) {
    XSync(d, False);
    printf("[glx-probe] step=%s xerrors=%d\n", step, xerrors);
    return xerrors == 0;
}

int main(int argc, char **argv) {
    enum { LEGACY, ATTRIBS33, CORE43, COMPAT43, SINGLE43, HIGH46 } mode = LEGACY;
    if (argc > 1) {
        if (strcmp(argv[1], "attribs") == 0) mode = ATTRIBS33;
        else if (strcmp(argv[1], "core43") == 0) mode = CORE43;
        else if (strcmp(argv[1], "compat43") == 0) mode = COMPAT43;
        else if (strcmp(argv[1], "single43") == 0) mode = SINGLE43;
        else if (strcmp(argv[1], "high46") == 0) mode = HIGH46;
        else {
            fprintf(stderr, "Usage: %s [attribs|core43|compat43|single43|high46]\n", argv[0]);
            return 64;
        }
    }
    setbuf(stdout, NULL);
    /* Keep the full receipt as one literal in the ELF string table. */
    printf("%s\n", "CINTERFORGE_GLX_PROBE_SHA=" CINTERFORGE_GLX_PROBE_SOURCE_SHA);
    XSetErrorHandler(on_x_error);
    Display *d = XOpenDisplay(NULL);
    if (!d) { fprintf(stderr, "[glx-probe] XOpenDisplay failed\n"); return 2; }
    int screen = DefaultScreen(d), major = 0, minor = 0, count = 0;
    int rc = 0;
    GLXFBConfig *configs = NULL;
    XVisualInfo *vi = NULL;
    Colormap cmap = 0;
    Window window = 0;
    GLXContext ctx = NULL;
    int made_current = 0;
    printf("[glx-probe] display=%p connection=%d screen=%d\n", (void *)d, ConnectionNumber(d), screen);
    if (!glXQueryVersion(d, &major, &minor)) { rc = 3; goto cleanup; }
    printf("[glx-probe] GLX version=%d.%d\n", major, minor);
    int attrs[] = { GLX_X_RENDERABLE, True, GLX_DRAWABLE_TYPE, GLX_WINDOW_BIT,
                    GLX_RENDER_TYPE, GLX_RGBA_BIT, GLX_DOUBLEBUFFER, True, None };
    /* mesa-demos glxinfo chooses single-buffered RGBA configs first, while
     * the original probe chose double-buffered configs. Test that difference
     * without changing working FEX graphics thunks. */
    int single_attrs[] = { GLX_RENDER_TYPE, GLX_RGBA_BIT,
                           GLX_RED_SIZE, 1, GLX_GREEN_SIZE, 1,
                           GLX_BLUE_SIZE, 1, GLX_DOUBLEBUFFER, False, None };
    int single_buffer = mode == SINGLE43 || mode == HIGH46;
    printf("[glx-probe] fbconfig-selection=%s\n",
           single_buffer ? "glxinfo-single-buffer" : "double-buffer");
    configs = glXChooseFBConfig(d, screen,
                                single_buffer ? single_attrs : attrs, &count);
    if (!checked(d, "choose-fbconfig") || !configs || count <= 0) { rc = 4; goto cleanup; }
    GLXFBConfig chosen = configs[0];
    int id = -1, visual = -1;
    glXGetFBConfigAttrib(d, chosen, GLX_FBCONFIG_ID, &id);
    glXGetFBConfigAttrib(d, chosen, GLX_VISUAL_ID, &visual);
    printf("[glx-probe] configs=%d chosen=%p id=0x%x visual=0x%x\n", count, (void *)chosen, id, visual);
    if (!checked(d, "inspect-fbconfig")) { rc = 5; goto cleanup; }
    vi = glXGetVisualFromFBConfig(d, chosen);
    if (!vi || !checked(d, "get-visual")) { rc = 6; goto cleanup; }
    cmap = XCreateColormap(d, RootWindow(d, screen), vi->visual, AllocNone);
    XSetWindowAttributes wa = {0};
    wa.colormap = cmap;
    wa.border_pixel = 0;
    wa.event_mask = StructureNotifyMask;
    window = XCreateWindow(d, RootWindow(d, screen), 0, 0, 256, 256, 0,
                           vi->depth, InputOutput, vi->visual,
                           CWColormap | CWBorderPixel | CWEventMask, &wa);
    XMapWindow(d, window);
    if (!checked(d, "create-window")) { rc = 7; goto cleanup; }

    if (mode == LEGACY) {
        printf("[glx-probe] create_context mode=legacy\n");
        ctx = glXCreateNewContext(d, chosen, GLX_RGBA_TYPE, NULL, True);
    } else {
        PFNGLXCREATECONTEXTATTRIBSARBPROC create =
            (PFNGLXCREATECONTEXTATTRIBSARBPROC)glXGetProcAddressARB(
                (const GLubyte *)"glXCreateContextAttribsARB");
        if (!create) { fprintf(stderr, "[glx-probe] attribs entry unavailable\n"); rc = 8; goto cleanup; }
        int version_major = mode == ATTRIBS33 ? 3 : 4;
        int version_minor = mode == ATTRIBS33 ? 3 : mode == HIGH46 ? 6 : 3;
        int profile = (mode == CORE43 || mode == SINGLE43 || mode == HIGH46)
                      ? GLX_CONTEXT_CORE_PROFILE_BIT_ARB
                      : GLX_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB;
        int ctx_attrs[] = { GLX_CONTEXT_MAJOR_VERSION_ARB, version_major,
                            GLX_CONTEXT_MINOR_VERSION_ARB, version_minor,
                            GLX_CONTEXT_PROFILE_MASK_ARB, profile, None };
        printf("[glx-probe] create_context mode=%s version=%d.%d\n",
               mode == CORE43 || mode == SINGLE43 || mode == HIGH46 ? "core" : mode == COMPAT43 ? "compat" : "attribs", version_major, version_minor);
        ctx = create(d, chosen, NULL, True, ctx_attrs);
    }
    printf("[glx-probe] created_context=%p\n", (void *)ctx);
    if (!checked(d, "create-context") || !ctx) { rc = 9; goto cleanup; }
    made_current = glXMakeCurrent(d, window, ctx) == True;
    printf("[glx-probe] make_current=%d\n", made_current);
    if (!checked(d, "make-current") || !made_current) { rc = 10; goto cleanup; }
    const GLubyte *vendor = glGetString(GL_VENDOR);
    const GLubyte *renderer = glGetString(GL_RENDERER);
    const GLubyte *version = glGetString(GL_VERSION);
    printf("[glx-probe] GL_VENDOR=%s\n", vendor ? (const char *)vendor : "(null)");
    printf("[glx-probe] GL_RENDERER=%s\n", renderer ? (const char *)renderer : "(null)");
    printf("[glx-probe] GL_VERSION=%s\n", version ? (const char *)version : "(null)");
    if (!vendor || !renderer || !version) { rc = 11; goto cleanup; }
    glViewport(0, 0, 256, 256);
    glClearColor(0.1f, 0.2f, 0.4f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    GLenum gl_error = glGetError();
    printf("[glx-probe] clear/finish GL error=0x%x\n", (unsigned)gl_error);
    if (gl_error != GL_NO_ERROR) { rc = 12; goto cleanup; }
    glXSwapBuffers(d, window);
    if (!checked(d, "swap-buffers")) { rc = 13; goto cleanup; }
    printf("[glx-probe] accelerated-context-path-complete\n");
cleanup:
    if (made_current) glXMakeCurrent(d, None, NULL);
    if (ctx) glXDestroyContext(d, ctx);
    if (window) XDestroyWindow(d, window);
    if (cmap) XFreeColormap(d, cmap);
    if (vi) XFree(vi);
    if (configs) XFree(configs);
    XSync(d, False);
    XCloseDisplay(d);
    printf("[glx-probe] exit=%d xerrors=%d\n", rc, xerrors);
    return rc ? rc : (xerrors ? 14 : 0);
}
