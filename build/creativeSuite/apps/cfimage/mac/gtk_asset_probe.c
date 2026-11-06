/* Validate GTK's *runtime* resource discovery, not merely copied files.
 * Build with gtk+-3.0 from Setup and run using the final .app launcher env.
 */
#include <gtk/gtk.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    static const char *names[] = {
        "image-missing", "folder", "document-open", "dialog-error",
        "document-open-symbolic", "folder-symbolic"
    };
    GtkIconTheme *theme;
    guint i;
    int failures = 0;
    GtkWidget *chooser;
    GtkWidget *image;
    GtkRequisition minimum, natural;
    GSettingsSchemaSource *schema_source;
    GSettingsSchema *chooser_schema;
    const char *schema_dir = g_getenv("GSETTINGS_SCHEMA_DIR");
    GError *schema_error = NULL;

    if (!schema_dir || !*schema_dir) {
        fputs("[GTK assets] GSETTINGS_SCHEMA_DIR is not set\n", stderr);
        return 2;
    }
    schema_source = g_settings_schema_source_new_from_directory(
        schema_dir, NULL, FALSE, &schema_error);
    if (!schema_source) {
        fprintf(stderr, "[GTK assets] bundled GSettings schemas unavailable: %s\n",
                schema_error ? schema_error->message : "unknown error");
        g_clear_error(&schema_error);
        return 2;
    }
    chooser_schema = g_settings_schema_source_lookup(
        schema_source, "org.gtk.Settings.FileChooser", FALSE);
    if (!chooser_schema) {
        fprintf(stderr, "[GTK assets] missing bundled org.gtk.Settings.FileChooser schema in %s\n",
                schema_dir);
        g_settings_schema_source_unref(schema_source);
        return 2;
    }
    g_settings_schema_unref(chooser_schema);
    g_settings_schema_source_unref(schema_source);

    if (!gtk_init_check(&argc, &argv)) {
        fputs("[GTK assets] gtk_init_check failed (no usable GTK display)\n", stderr);
        return 2;
    }
    theme = gtk_icon_theme_get_default();
    if (!theme) {
        fputs("[GTK assets] GTK has no default icon theme\n", stderr);
        return 2;
    }
    for (i = 0; i < G_N_ELEMENTS(names); ++i) {
        GError *error = NULL;
        GdkPixbuf *pixbuf = gtk_icon_theme_load_icon(
            theme, names[i], 24, GTK_ICON_LOOKUP_FORCE_SIZE, &error);
        if (!pixbuf || error) {
            fprintf(stderr, "[GTK assets] icon '%s' cannot render: %s\n",
                    names[i], error ? error->message : "missing icon/decoder");
            failures++;
        }
        if (pixbuf) g_object_unref(pixbuf);
        g_clear_error(&error);
    }
    /* Exercise the real GtkImage/GIcon and file chooser template paths.
     * gtk_icon_theme_load_icon() alone does not execute GTK's
     * ensure_surface_for_gicon(), which is where standalone GIMP aborts.
     * Realizing/asking for sizes forces GTK to resolve the icons used by
     * a file chooser before GIMP builds its main display shell.
     */
    image = gtk_image_new_from_icon_name("image-missing", GTK_ICON_SIZE_BUTTON);
    gtk_widget_get_preferred_size(image, &minimum, &natural);
    g_object_unref(g_object_ref_sink(image));

    chooser = gtk_file_chooser_dialog_new("GTK asset validation", NULL,
                                         GTK_FILE_CHOOSER_ACTION_OPEN,
                                         "_Cancel", GTK_RESPONSE_CANCEL,
                                         NULL);
    if (!chooser) {
        fputs("[GTK assets] failed to construct file chooser\n", stderr);
        return 1;
    }
    gtk_widget_get_preferred_size(chooser, &minimum, &natural);
    gtk_widget_destroy(chooser);
    while (g_main_context_pending(NULL))
        g_main_context_iteration(NULL, FALSE);

    if (failures) {
        fprintf(stderr, "[GTK assets] %d icon render failures. Check bundled themes, GdkPixbuf SVG loader and its dependent libraries.\n", failures);
        return 1;
    }
    fputs("[GTK assets] GTK icon rendering OK (PNG and symbolic SVG)\n", stderr);
    return 0;
}
