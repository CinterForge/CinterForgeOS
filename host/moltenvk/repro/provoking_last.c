/*
 * Flat varyings with the last vertex provoking (KosmicKrisp only; STEAMAC-G).
 *
 * zink keeps GL's last provoking vertex (VK_EXT_provoking_vertex), and Metal only has the first. KosmicKrisp unrolls a
 * draw whose fragment shader reads a flat varying into indices that rotate the last vertex first; the unroll kernel
 * ended the render pass around every such draw: its attachments stored, a compute pass, the attachments loaded again.
 * Steam's web helper composites its first frame like this, 12 rectangles into its LINEAR window buffer (host
 * memory): 13 render passes over the whole 2560x1600 window took 43 ms of GPU time on an M1 Max, and M3 and later GPUs
 * lost the device right there (the GPU's recovery from a command buffer it could not preempt).
 *
 * Draws flat-shaded strips (also an instanced one), fans and lists into a 2560x1600 B8G8R8A8 LINEAR host pointer import
 * like zink's window buffer, one render pass, and checks the last vertex's color in both triangles of each cell. Run with
 * MESA_KK_DEBUG=cbs (child process): the command buffer's record must show one render pass and no compute encoder (the
 * indices of direct draws without an index buffer are known when recording); the GPU time comes from that record.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vulkan/vulkan.h>

#define CK(x) do { VkResult r_ = (x); if (r_) { printf("FAIL %s = %d (line %d)\n", #x, r_, __LINE__); exit(1); } } while (0)
#define W 2560
#define H 1600

static VkDevice dev;

static VkShaderModule load_shader(const char *dir, const char *name)
{
	char path[4096];
	snprintf(path, sizeof(path), "%s/%s.spv", dir, name);
	FILE *f = fopen(path, "rb");
	if (!f) { printf("FAIL open %s\n", path); exit(1); }
	fseek(f, 0, SEEK_END);
	long size = ftell(f);
	fseek(f, 0, SEEK_SET);
	uint32_t *code = malloc(size);
	if (fread(code, 1, size, f) != (size_t)size) { printf("FAIL read %s\n", path); exit(1); }
	fclose(f);
	VkShaderModuleCreateInfo ci = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = size, .pCode = code };
	VkShaderModule m;
	CK(vkCreateShaderModule(dev, &ci, NULL, &m));
	free(code);
	return m;
}

/* topology, cell sample points (cell-local, y up as in the vertex shader) and the vertex provoking there */
static const struct {
	VkPrimitiveTopology topology;
	float x[2], y[2];
	uint32_t k[2];
} topologies[3] = {
	{ VK_PRIMITIVE_TOPOLOGY_TRIANGLE_STRIP, { 0.2f, 0.8f }, { 0.2f, 0.8f }, { 2, 3 } },
	{ VK_PRIMITIVE_TOPOLOGY_TRIANGLE_FAN, { 0.8f, 0.2f }, { 0.2f, 0.8f }, { 2, 3 } },
	{ VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST, { 0.2f, 0.8f }, { 0.2f, 0.8f }, { 2, 5 } },
};

/* cells 0-5 strips, 6-7 one instanced strip draw, 8-9 fans, 10-11 lists */
static uint32_t cell_topology(uint32_t cell)
{
	return cell < 8 ? 0 : cell < 10 ? 1 : 2;
}

static int child(const char *spv_dir)
{
	VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "provoking_last",
		.apiVersion = VK_API_VERSION_1_3 };
	VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app };
	VkInstance inst;
	CK(vkCreateInstance(&ici, NULL, &inst));
	uint32_t n = 1;
	VkPhysicalDevice pd;
	if (vkEnumeratePhysicalDevices(inst, &n, &pd) < 0 || !n) { printf("FAIL no physical device\n"); return 1; }

	VkPhysicalDeviceProvokingVertexFeaturesEXT pvf = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROVOKING_VERTEX_FEATURES_EXT };
	VkPhysicalDeviceVulkan13Features v13 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, &pvf };
	VkPhysicalDeviceFeatures2 f2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &v13 };
	vkGetPhysicalDeviceFeatures2(pd, &f2);
	if (!pvf.provokingVertexLast || !v13.dynamicRendering) {
		printf("SKIP provoking_last: no provokingVertexLast\n");
		return 0;
	}
	VkPhysicalDeviceExternalMemoryHostPropertiesEXT hostp = {
		VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_MEMORY_HOST_PROPERTIES_EXT };
	VkPhysicalDeviceProperties2 p2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &hostp };
	vkGetPhysicalDeviceProperties2(pd, &p2);

	VkPhysicalDeviceProvokingVertexFeaturesEXT pv_on = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROVOKING_VERTEX_FEATURES_EXT,
		.provokingVertexLast = VK_TRUE };
	VkPhysicalDeviceVulkan13Features v13_on = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES, &pv_on,
		.dynamicRendering = VK_TRUE };
	const char *exts[] = { VK_EXT_PROVOKING_VERTEX_EXTENSION_NAME, VK_EXT_EXTERNAL_MEMORY_HOST_EXTENSION_NAME };
	float prio = 1;
	VkDeviceQueueCreateInfo qci = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueCount = 1, .pQueuePriorities = &prio };
	VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &v13_on, .queueCreateInfoCount = 1,
		.pQueueCreateInfos = &qci, .enabledExtensionCount = 2, .ppEnabledExtensionNames = exts };
	CK(vkCreateDevice(pd, &dci, NULL, &dev));
	VkQueue queue;
	vkGetDeviceQueue(dev, 0, 0, &queue);
	PFN_vkGetMemoryHostPointerPropertiesEXT get_host_ptr_props =
		(PFN_vkGetMemoryHostPointerPropertiesEXT)vkGetDeviceProcAddr(dev, "vkGetMemoryHostPointerPropertiesEXT");

	/* the window buffer: LINEAR B8G8R8A8 in imported host memory, as virglrenderer gives zink */
	VkExternalMemoryImageCreateInfo ext = { VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
		.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT };
	VkImageCreateInfo ici2 = { VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, &ext, .imageType = VK_IMAGE_TYPE_2D,
		.format = VK_FORMAT_B8G8R8A8_UNORM, .extent = { W, H, 1 }, .mipLevels = 1, .arrayLayers = 1,
		.samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_LINEAR,
		.usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT };
	VkImage image;
	CK(vkCreateImage(dev, &ici2, NULL, &image));
	VkMemoryRequirements req;
	vkGetImageMemoryRequirements(dev, image, &req);
	VkDeviceSize align = hostp.minImportedHostPointerAlignment;
	VkDeviceSize host_size = (req.size + align - 1) / align * align;
	void *host = mmap(NULL, host_size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANON, -1, 0);
	if (host == MAP_FAILED) { printf("FAIL mmap\n"); return 1; }
	VkMemoryHostPointerPropertiesEXT hp = { VK_STRUCTURE_TYPE_MEMORY_HOST_POINTER_PROPERTIES_EXT };
	CK(get_host_ptr_props(dev, VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, host, &hp));
	uint32_t type = __builtin_ctz(req.memoryTypeBits & hp.memoryTypeBits);
	VkImportMemoryHostPointerInfoEXT import = { VK_STRUCTURE_TYPE_IMPORT_MEMORY_HOST_POINTER_INFO_EXT,
		.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_HOST_ALLOCATION_BIT_EXT, .pHostPointer = host };
	VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, &import, .allocationSize = host_size,
		.memoryTypeIndex = type };
	VkDeviceMemory mem;
	CK(vkAllocateMemory(dev, &ai, NULL, &mem));
	CK(vkBindImageMemory(dev, image, mem, 0));
	VkImageSubresource sub = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0 };
	VkSubresourceLayout layout;
	vkGetImageSubresourceLayout(dev, image, &sub, &layout);
	VkImageViewCreateInfo vci = { VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = image,
		.viewType = VK_IMAGE_VIEW_TYPE_2D, .format = VK_FORMAT_B8G8R8A8_UNORM,
		.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } };
	VkImageView view;
	CK(vkCreateImageView(dev, &vci, NULL, &view));

	VkPushConstantRange pcr = { VK_SHADER_STAGE_VERTEX_BIT, 0, 4 };
	VkPipelineLayoutCreateInfo plci = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .pushConstantRangeCount = 1,
		.pPushConstantRanges = &pcr };
	VkPipelineLayout pl;
	CK(vkCreatePipelineLayout(dev, &plci, NULL, &pl));
	VkShaderModule vs = load_shader(spv_dir, "flat_last.vert"), fs = load_shader(spv_dir, "flat_last.frag");
	VkPipelineShaderStageCreateInfo stages[2] = {
		{ VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_VERTEX_BIT, .module = vs, .pName = "main" },
		{ VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = fs, .pName = "main" },
	};
	VkPipeline pipes[3];
	for (uint32_t t = 0; t < 3; t++) {
		VkPipelineVertexInputStateCreateInfo vi = { VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO };
		VkPipelineInputAssemblyStateCreateInfo ia = { VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
			.topology = topologies[t].topology };
		VkViewport vp = { 0, 0, W, H, 0, 1 };
		VkRect2D sc = { { 0, 0 }, { W, H } };
		VkPipelineViewportStateCreateInfo vps = { VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
			.viewportCount = 1, .pViewports = &vp, .scissorCount = 1, .pScissors = &sc };
		VkPipelineRasterizationProvokingVertexStateCreateInfoEXT pvs = {
			VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_PROVOKING_VERTEX_STATE_CREATE_INFO_EXT,
			.provokingVertexMode = VK_PROVOKING_VERTEX_MODE_LAST_VERTEX_EXT };
		VkPipelineRasterizationStateCreateInfo rs = { VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO, &pvs,
			.polygonMode = VK_POLYGON_MODE_FILL, .cullMode = VK_CULL_MODE_NONE, .lineWidth = 1 };
		VkPipelineMultisampleStateCreateInfo ms = { VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
			.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT };
		VkPipelineColorBlendAttachmentState cba = { .colorWriteMask = 0xf };
		VkPipelineColorBlendStateCreateInfo cb = { VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
			.attachmentCount = 1, .pAttachments = &cba };
		VkFormat format = VK_FORMAT_B8G8R8A8_UNORM;
		VkPipelineRenderingCreateInfo prci = { VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,
			.colorAttachmentCount = 1, .pColorAttachmentFormats = &format };
		VkGraphicsPipelineCreateInfo gci = { VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO, &prci, .stageCount = 2,
			.pStages = stages, .pVertexInputState = &vi, .pInputAssemblyState = &ia, .pViewportState = &vps,
			.pRasterizationState = &rs, .pMultisampleState = &ms, .pColorBlendState = &cb, .layout = pl };
		CK(vkCreateGraphicsPipelines(dev, VK_NULL_HANDLE, 1, &gci, NULL, &pipes[t]));
	}

	VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
	VkCommandPool pool;
	CK(vkCreateCommandPool(dev, &cpci, NULL, &pool));
	VkCommandBufferAllocateInfo cbai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool,
		.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
	VkCommandBuffer cmd;
	CK(vkAllocateCommandBuffers(dev, &cbai, &cmd));
	VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
		.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
	CK(vkBeginCommandBuffer(cmd, &bi));
	VkImageMemoryBarrier b = { VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
		.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
		.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = image,
		.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 } };
	vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, 0, 0, NULL,
			     0, NULL, 1, &b);
	VkRenderingAttachmentInfo att = { VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO, .imageView = view,
		.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL, .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR,
		.storeOp = VK_ATTACHMENT_STORE_OP_STORE };
	VkRenderingInfo ri = { VK_STRUCTURE_TYPE_RENDERING_INFO, .renderArea = { { 0, 0 }, { W, H } }, .layerCount = 1,
		.colorAttachmentCount = 1, .pColorAttachments = &att };
	vkCmdBeginRendering(cmd, &ri);
	for (uint32_t cell = 0; cell < 12; cell++) {
		uint32_t t = cell_topology(cell);
		vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_GRAPHICS, pipes[t]);
		vkCmdPushConstants(cmd, pl, VK_SHADER_STAGE_VERTEX_BIT, 0, 4, &t);
		if (cell == 6) {
			vkCmdDraw(cmd, 4, 2, cell * 8, 0); /* cells 6 and 7 */
			cell++;
		} else {
			vkCmdDraw(cmd, t == 2 ? 6 : 4, 1, cell * 8, 0);
		}
	}
	vkCmdEndRendering(cmd);
	CK(vkEndCommandBuffer(cmd));
	VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cmd };
	CK(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE));
	CK(vkQueueWaitIdle(queue));

	int fails = 0;
	for (uint32_t cell = 0; cell < 12; cell++) {
		uint32_t t = cell_topology(cell);
		for (uint32_t tri = 0; tri < 2; tri++) {
			float u = (cell % 4 + 0.05f + topologies[t].x[tri] * 0.9f) / 4.0f;
			float v = (cell / 4 + 0.05f + topologies[t].y[tri] * 0.9f) / 3.0f;
			uint32_t x = (uint32_t)(u * W), y = (uint32_t)(v * H);
			uint32_t got = *(uint32_t *)((char *)host + layout.offset + y * layout.rowPitch + x * 4);
			uint32_t want = 0xff000000u | (cell * 16 + topologies[t].k[tri] + 1) << 8 | (t + 1) * 40;
			if (got != want) {
				printf("FAIL cell %u (%s) triangle %u: color 0x%08x, want vertex %u's 0x%08x\n", cell,
				       t == 0 ? "strip" : t == 1 ? "fan" : "list", tri, got, topologies[t].k[tri], want);
				fails++;
			}
		}
	}
	printf("%-4s last vertex provoking in 24 triangles (strips, an instanced strip, fans, lists)\n", fails ? "FAIL" : "OK");
	fflush(stdout);
	/* KosmicKrisp logs the record when the command buffer's feedback comes, the device's destruction waits for it */
	vkDestroyDevice(dev, NULL);
	return fails != 0;
}

int main(int argc, char **argv)
{
	if (argc < 2) { printf("usage: %s <spv dir> [child]\n", argv[0]); return 2; }
	if (argc > 2 && !strcmp(argv[2], "child"))
		return child(argv[1]);

	/* The child logs its command buffer's record (MESA_KK_DEBUG=cbs) on stderr */
	char err_path[] = "/tmp/provoking_last.XXXXXX";
	int fd = mkstemp(err_path);
	if (fd < 0) { printf("FAIL mkstemp\n"); return 1; }
	close(fd);
	char cmd[8192];
	snprintf(cmd, sizeof(cmd), "MESA_KK_DEBUG=cbs '%s' '%s' child 2> '%s'", argv[0], argv[1], err_path);
	fflush(stdout);
	int status = system(cmd);
	FILE *f = fopen(err_path, "r");
	char line[4096], record[4096] = "";
	while (f && fgets(line, sizeof(line), f)) {
		if (strstr(line, "kk: command buffer") && strstr(line, "of provoking_last") && strstr(line, "draws"))
			snprintf(record, sizeof(record), "%s", line);
	}
	if (f)
		fclose(f);
	unlink(err_path);
	int fails = status != 0;
	if (status)
		printf("FAIL child exit status %d\n", status);
	/* The draws' command buffer */
	int one_pass = strstr(record, ": 1 render passes, 0 compute encoders, 11 draws") != NULL;
	const char *shown = strstr(record, "provoking_last: ");
	printf("%-4s draws in one render pass, no compute pass (MESA_KK_DEBUG=cbs: %s)\n", one_pass ? "OK" : "FAIL",
	       shown ? shown + strlen("provoking_last: ") : "no record\n");
	fails += !one_pass;
	if (fails) { printf("provoking_last: %d failure(s)\n", fails); return 1; }
	return 0;
}
