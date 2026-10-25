/* Shared GL rendering checks for the existing CinterForgeOS GLX/WGL probe. */
#ifndef CINTERFORGE_GL_PROBE_RENDER_H
#define CINTERFORGE_GL_PROBE_RENDER_H
#include <GL/gl.h>
#include <stdio.h>
static int cf_gl_render_check(const char *platform) {
    const GLubyte *vendor = glGetString(GL_VENDOR);
    const GLubyte *renderer = glGetString(GL_RENDERER);
    const GLubyte *version = glGetString(GL_VERSION);
    printf("[%s-probe] GL_VENDOR=%s\n", platform, vendor ? (const char *)vendor : "(null)");
    printf("[%s-probe] GL_RENDERER=%s\n", platform, renderer ? (const char *)renderer : "(null)");
    printf("[%s-probe] GL_VERSION=%s\n", platform, version ? (const char *)version : "(null)");
    if (!vendor || !renderer || !version) return 11;
    glViewport(0, 0, 256, 256);
    glClearColor(0.1f, 0.2f, 0.4f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();
    GLenum error = glGetError();
    printf("[%s-probe] clear/finish GL error=0x%x\n", platform, (unsigned)error);
    return error == GL_NO_ERROR ? 0 : 12;
}
#endif
