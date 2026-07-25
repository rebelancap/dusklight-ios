// DuskImmersive.m — visionOS stereoscopic "3D screen" render loop.
//
// Faithful port of Shipwright-ios's SohImmersive.m (STEREO-3D-GUIDE §6),
// descended from the proven vkQuake-ios loop. The loop shape is LOAD-BEARING
// (guide §2.3 / §9): frame pacing via cp_time_wait_until(optimal_input_time) + a
// per-frame ar device anchor set on the drawable + cleared/written depth + a
// command queue created from the drawable's OWN device + @autoreleasepool.
// Remove any one and the panel goes black or the process aborts.
//
// M2: engine eye-textures (Dusk3D_GetEyeMTLTexture) are NULL, so the panel shows
// the built-in magenta/teal test pattern -- proving the whole CompositorServices
// path (open space, anchor, pace, present, exit) before any aurora eye-render
// work (M3). M4 fills both eyes with the game's stereo images.

#import "DuskImmersive.h"
#import <Metal/Metal.h>
#import <ARKit/ARKit.h>
#import <simd/simd.h>
#import <pthread.h>
#import <IOSurface/IOSurfaceRef.h>

volatile int gDusk3DStop = 0;
volatile int gDusk3DRunning = 0;
static int dusk3d_frameCount = 0;

// --- phase-lock (guide §3.7) -------------------------------------------------
static pthread_mutex_t dusk3d_paceMutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t dusk3d_paceCond = PTHREAD_COND_INITIALIZER;
static int dusk3d_paceReady = 0;

void dusk3d_pace_signal(void) {
    pthread_mutex_lock(&dusk3d_paceMutex);
    dusk3d_paceReady = 1; // capped at 1 -- never a backlog
    pthread_cond_signal(&dusk3d_paceCond);
    pthread_mutex_unlock(&dusk3d_paceMutex);
}

void dusk3d_wait_for_compositor_frame(void) {
    pthread_mutex_lock(&dusk3d_paceMutex);
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_nsec += 50 * 1000 * 1000; // 50 ms floor: loop death must not hang the game
    if (ts.tv_nsec >= 1000000000L) {
        ts.tv_sec += 1;
        ts.tv_nsec -= 1000000000L;
    }
    while (!dusk3d_paceReady) {
        if (pthread_cond_timedwait(&dusk3d_paceCond, &dusk3d_paceMutex, &ts) != 0)
            break; // timeout
    }
    dusk3d_paceReady = 0;
    pthread_mutex_unlock(&dusk3d_paceMutex);
}

// --- world-lock math ---------------------------------------------------------
static simd_float4x4 dusk3d_translate(float x, float y, float z) {
    simd_float4x4 m = matrix_identity_float4x4;
    m.columns[3] = simd_make_float4(x, y, z, 1.0f);
    return m;
}
static simd_float4x4 dusk3d_scale(float x, float y, float z) {
    simd_float4x4 m = matrix_identity_float4x4;
    m.columns[0].x = x;
    m.columns[1].y = y;
    m.columns[2].z = z;
    return m;
}

// Panel placement: captured from the head pose once tracking converges, then
// world-locked; recomputed from the frozen head each frame so live tuning of
// distance/size moves the panel in real time.
static float dusk3d_screenDist = 3.6f;   // metres from the captured head position
static float dusk3d_screenHalfW = 2.75f;
static float dusk3d_screenHalfH = 1.55f;
static float dusk3d_screenHeight = 0.0f; // metres above eye level

void Dusk3D_SetPanel(float dist, float halfW, float halfH) {
    if (dist >= 1.0f && dist <= 8.0f)
        dusk3d_screenDist = dist;
    if (halfW >= 0.6f && halfW <= 4.0f)
        dusk3d_screenHalfW = halfW;
    if (halfH >= 0.4f && halfH <= 3.0f)
        dusk3d_screenHalfH = halfH;
}
void Dusk3D_SetHeight(float h) {
    if (h >= -1.5f && h <= 10.0f)
        dusk3d_screenHeight = h;
}

static bool dusk3d_haveScreenAnchor = false;
static simd_float4x4 dusk3d_frozenHead;

void Dusk3D_Recenter(void) {
    dusk3d_haveScreenAnchor = false; // next tracked frame re-captures the pose
}

// Surroundings dimming: fullscreen black layer under the panel. Perceptual curve
// 1-(1-d)^2.2 -- linear "doesn't get dark until 80%" (vkQuake-measured).
static float dusk3d_dimLevel = 0.0f;
void Dusk3D_SetDim(float dim) {
    dim = (dim < 0.0f) ? 0.0f : (dim > 1.0f) ? 1.0f : dim;
    dusk3d_dimLevel = 1.0f - powf(1.0f - dim, 2.2f);
}

static simd_float4x4 dusk3d_make_screen_anchor(simd_float4x4 originFromDevice) {
    simd_float3 headPos = originFromDevice.columns[3].xyz;
    simd_float3 fwd = -originFromDevice.columns[2].xyz; // gaze forward
    fwd.y = 0.0f;                                       // level (no pitch/roll)
    float len = simd_length(fwd);
    fwd = (len < 1e-4f) ? simd_make_float3(0, 0, -1) : fwd / len;

    simd_float3 pos = headPos + fwd * dusk3d_screenDist;
    pos.y += dusk3d_screenHeight;
    simd_float3 normal = simd_normalize(headPos - pos);
    simd_float3 up = simd_make_float3(0, 1, 0);
    simd_float3 right = simd_normalize(simd_cross(up, normal));
    up = simd_cross(normal, right);

    simd_float4x4 m;
    m.columns[0] = simd_make_float4(right, 0.0f);
    m.columns[1] = simd_make_float4(up, 0.0f);
    m.columns[2] = simd_make_float4(normal, 0.0f);
    m.columns[3] = simd_make_float4(pos, 1.0f);
    return m;
}

// Persistent mipmapped per-eye sampling copies of the engine's eye textures.
static id<MTLTexture> dusk3d_eyeCopy[2];
// The engine's eye IOSurfaces wrapped as MTLTextures on the drawable's device
// (cached by IOSurface identity so we only wrap on change).
static IOSurfaceRef dusk3d_eyeSrcSurface[2];
static id<MTLTexture> dusk3d_eyeSrcTex[2];

// Test pattern shown until the engine's eye framebuffers exist (M2 gate: the
// compositor path is verifiable in the sim before any aurora work lands).
static id<MTLTexture> dusk3d_testPattern;
static id<MTLTexture> dusk3d_make_test_pattern(id<MTLDevice> dev) {
    const int W = 640, H = 360, TILE = 40;
    MTLTextureDescriptor* td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm
                                                                                  width:W
                                                                                 height:H
                                                                              mipmapped:NO];
    td.usage = MTLTextureUsageShaderRead;
    id<MTLTexture> t = [dev newTextureWithDescriptor:td];
    uint32_t* px = malloc(W * H * 4);
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            bool a = ((x / TILE) + (y / TILE)) & 1;
            // magenta/teal checker: unmistakably "test pattern", never "bug"
            px[y * W + x] = a ? 0xFFB4287D : 0xFF7DB428;
        }
    }
    [t replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0 withBytes:px bytesPerRow:W * 4];
    free(px);
    return t;
}

// One-shot fidelity report: measures the ACTUAL panel supersample ratio (drawable
// px/FOV vs panel angular size vs game texture). Written to
// Documents/vp3d-fidelity.log (OTA-readable). Guide §11.
static bool dusk3d_fidelityLogged = false;
static void dusk3d_log_fidelity(cp_drawable_t drawable, id<MTLTexture> gameTex) {
    if (dusk3d_fidelityLogged || gameTex == nil)
        return;
    cp_view_t view = cp_drawable_get_view(drawable, 0);
    MTLViewport vp = cp_view_texture_map_get_viewport(cp_view_get_view_texture_map(view));
    simd_float4x4 proj = matrix_identity_float4x4;
    if (__builtin_available(visionOS 2.0, *))
        proj = cp_drawable_compute_projection(drawable, cp_axis_direction_convention_right_up_back, 0);
    double m00 = fabs(proj.columns[0].x), m11 = fabs(proj.columns[1].y);
    double fovH = (m00 > 1e-6) ? 2.0 * atan(1.0 / m00) : 0.0;
    double fovV = (m11 > 1e-6) ? 2.0 * atan(1.0 / m11) : 0.0;
    if (vp.width < 1 || vp.height < 1 || fovH < 1e-4 || fovV < 1e-4)
        return;
    double pxPerRadH = vp.width / fovH, pxPerRadV = vp.height / fovV;
    double panAngH = 2.0 * atan(dusk3d_screenHalfW / dusk3d_screenDist);
    double panAngV = 2.0 * atan(dusk3d_screenHalfH / dusk3d_screenDist);
    double footH = panAngH * pxPerRadH, footV = panAngV * pxPerRadV;
    double ssH = footH > 1 ? gameTex.width / footH : 0.0;
    double ssV = footV > 1 ? gameTex.height / footV : 0.0;

    NSString* docs = [NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES) firstObject];
    NSString* report = [NSString
        stringWithFormat:@"Dusklight Vision Pro 3D fidelity report\n"
                          "=======================================\n"
                          "Compositor drawable (per eye): %.0f x %.0f px\n"
                          "Per-eye FOV: %.1f x %.1f deg\n"
                          "Game render target (per eye): %lu x %lu px\n"
                          "Panel angular size: %.1f x %.1f deg\n"
                          "Panel footprint in drawable: %.0f x %.0f px\n"
                          "SUPERSAMPLE RATIO: %.2fx H, %.2fx V (%s)\n",
                         (double)vp.width, (double)vp.height, fovH * 180.0 / M_PI, fovV * 180.0 / M_PI,
                         (unsigned long)gameTex.width, (unsigned long)gameTex.height, panAngH * 180.0 / M_PI,
                         panAngV * 180.0 / M_PI, footH, footV, ssH, ssV,
                         (ssH >= 1.0 && ssV >= 1.0) ? "supersampling" : "UNDERSAMPLING"];
    [report writeToFile:[docs stringByAppendingPathComponent:@"vp3d-fidelity.log"]
             atomically:YES
               encoding:NSUTF8StringEncoding
                  error:NULL];
    NSLog(@"[Dusk3D] fidelity: drawable %.0fx%.0f/eye game %lux%lu supersample %.2fx/%.2fx", (double)vp.width,
          (double)vp.height, (unsigned long)gameTex.width, (unsigned long)gameTex.height, ssH, ssV);
    dusk3d_fidelityLogged = true;
}

// Panel quad + dim layer pipelines, compiled at runtime from drawable formats.
static id<MTLRenderPipelineState> dusk3d_pipeline;
static id<MTLRenderPipelineState> dusk3d_dimPipeline;
static id<MTLDepthStencilState> dusk3d_depthState;
static id<MTLDepthStencilState> dusk3d_dimDepthState;

static NSString* const kDusk3DQuadShader =
    @"#include <metal_stdlib>\n"
     "using namespace metal;\n"
     "struct VOut { float4 pos [[position]]; float2 uv; };\n"
     "vertex VOut dusk3d_vs(uint vid [[vertex_id]], constant float4x4& mvp [[buffer(0)]]) {\n"
     "  const float2 p[4] = { float2(-1,-1), float2(1,-1), float2(-1,1), float2(1,1) };\n"
     "  VOut o; o.pos = mvp * float4(p[vid], 0.0, 1.0);\n"
     "  o.uv = float2((p[vid].x+1.0)*0.5, 1.0-(p[vid].y+1.0)*0.5);\n"
     "  return o;\n"
     "}\n"
     "fragment float4 dusk3d_fs(VOut in [[stage_in]], texture2d<float> tex [[texture(0)]],\n"
     "                          constant float& srgbDecode [[buffer(0)]]) {\n"
     "  constexpr sampler s(filter::linear, mip_filter::linear, max_anisotropy(16));\n"
     "  float4 c = tex.sample(s, in.uv);\n"
     "  if (srgbDecode > 0.5) c.rgb = pow(c.rgb, float3(2.2));\n"
     "  return float4(c.rgb, 1.0);\n"
     "}\n"
     "vertex float4 dusk3d_dim_vs(uint vid [[vertex_id]]) {\n"
     "  const float2 p[3] = { float2(-1,-3), float2(3,1), float2(-1,1) };\n"
     "  return float4(p[vid], 0.9999, 1.0);\n"
     "}\n"
     "fragment float4 dusk3d_dim_fs(constant float& dim [[buffer(0)]]) {\n"
     "  return float4(0.0, 0.0, 0.0, dim);\n"
     "}\n";

static void dusk3d_build_pipeline(id<MTLDevice> dev, MTLPixelFormat colorFmt, MTLPixelFormat depthFmt) {
    NSError* err = nil;
    id<MTLLibrary> lib = [dev newLibraryWithSource:kDusk3DQuadShader options:nil error:&err];
    if (!lib) {
        NSLog(@"[Dusk3D] shader compile FAILED: %@", err.localizedDescription);
        return;
    }
    MTLRenderPipelineDescriptor* pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [lib newFunctionWithName:@"dusk3d_vs"];
    pd.fragmentFunction = [lib newFunctionWithName:@"dusk3d_fs"];
    pd.colorAttachments[0].pixelFormat = colorFmt;
    pd.depthAttachmentPixelFormat = depthFmt;
    dusk3d_pipeline = [dev newRenderPipelineStateWithDescriptor:pd error:&err];
    if (!dusk3d_pipeline) {
        NSLog(@"[Dusk3D] pipeline FAILED: %@", err.localizedDescription);
        return;
    }
    MTLDepthStencilDescriptor* dd = [MTLDepthStencilDescriptor new];
    dd.depthCompareFunction = MTLCompareFunctionAlways;
    dd.depthWriteEnabled = YES; // compositor reprojects on depth; must be real
    dusk3d_depthState = [dev newDepthStencilStateWithDescriptor:dd];

    MTLRenderPipelineDescriptor* dp = [MTLRenderPipelineDescriptor new];
    dp.vertexFunction = [lib newFunctionWithName:@"dusk3d_dim_vs"];
    dp.fragmentFunction = [lib newFunctionWithName:@"dusk3d_dim_fs"];
    dp.colorAttachments[0].pixelFormat = colorFmt;
    dp.colorAttachments[0].blendingEnabled = YES;
    dp.colorAttachments[0].sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
    dp.colorAttachments[0].destinationRGBBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
    dp.colorAttachments[0].sourceAlphaBlendFactor = MTLBlendFactorOne;
    dp.colorAttachments[0].destinationAlphaBlendFactor = MTLBlendFactorOne;
    dp.depthAttachmentPixelFormat = depthFmt;
    dusk3d_dimPipeline = [dev newRenderPipelineStateWithDescriptor:dp error:&err];
    if (!dusk3d_dimPipeline)
        NSLog(@"[Dusk3D] dim pipeline FAILED: %@", err.localizedDescription);
    MTLDepthStencilDescriptor* dd2 = [MTLDepthStencilDescriptor new];
    dd2.depthCompareFunction = MTLCompareFunctionAlways;
    dd2.depthWriteEnabled = YES;
    dusk3d_dimDepthState = [dev newDepthStencilStateWithDescriptor:dd2];
    NSLog(@"[Dusk3D] quad pipeline built (colorFmt=%lu depthFmt=%lu)", (unsigned long)colorFmt,
          (unsigned long)depthFmt);
}

void Dusk3D_Immersive_Run(cp_layer_renderer_t layer_renderer) {
    gDusk3DStop = 0;
    gDusk3DRunning = 1;
    int notifyEnded = 0; // only a system/Crown dismissal reconciles via Ended

    id<MTLCommandQueue> queue = nil;
    dusk3d_frameCount = 0;
    dusk3d_haveScreenAnchor = false; // re-center each time 3D is entered
    dusk3d_eyeCopy[0] = dusk3d_eyeCopy[1] = nil;
    dusk3d_fidelityLogged = false;

    ar_world_tracking_configuration_t wtc = ar_world_tracking_configuration_create();
    ar_world_tracking_provider_t wtp = ar_world_tracking_provider_create(wtc);
    ar_session_t arSession = ar_session_create();
    ar_data_providers_t providers = ar_data_providers_create_with_data_providers(wtp, NULL);
    ar_session_run(arSession, providers);

    NSLog(@"[Dusk3D] render loop started (ARKit world tracking running)");

    int running = 1;
    while (running) {
        if (gDusk3DStop) {
            NSLog(@"[Dusk3D] stop requested, exiting cleanly (frames=%d)", dusk3d_frameCount);
            running = 0;
            continue;
        }
        switch (cp_layer_renderer_get_state(layer_renderer)) {
            case cp_layer_renderer_state_paused:
                cp_layer_renderer_wait_until_running(layer_renderer);
                continue;
            case cp_layer_renderer_state_invalidated:
                NSLog(@"[Dusk3D] layer invalidated, exiting loop (frames=%d)", dusk3d_frameCount);
                notifyEnded = 1;
                running = 0;
                continue;
            case cp_layer_renderer_state_running:
            default:
                break;
        }

        @autoreleasepool {
            cp_frame_t frame = cp_layer_renderer_query_next_frame(layer_renderer);
            if (frame == NULL)
                continue;

            cp_frame_timing_t timing = cp_frame_predict_timing(frame);
            cp_frame_start_update(frame);
            cp_frame_end_update(frame);
            cp_time_wait_until(cp_frame_timing_get_optimal_input_time(timing));
            dusk3d_pace_signal(); // release the game loop for this compositor frame (§3.7)

            cp_frame_start_submission(frame);

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
            cp_drawable_t drawable = cp_frame_query_drawable(frame);
#pragma clang diagnostic pop
            if (drawable == NULL) {
                // A failed drawable query INVALIDATES the frame -- calling
                // end_submission on it ABORTS (guide §9.1 #4; the window-parking
                // geometry animation at entry +1.5s can make the compositor skip
                // a drawable). Just drop it.
                continue;
            }

            if (queue == nil) {
                id<MTLTexture> t0 = cp_drawable_get_color_texture(drawable, 0);
                queue = [t0.device newCommandQueue];
                dusk3d_build_pipeline(t0.device, t0.pixelFormat,
                                      cp_drawable_get_depth_texture(drawable, 0).pixelFormat);
                dusk3d_testPattern = dusk3d_make_test_pattern(t0.device);
                NSLog(@"[Dusk3D] drawable %lux%lu views=%zu colorFmt=%lu", (unsigned long)t0.width,
                      (unsigned long)t0.height, cp_drawable_get_view_count(drawable),
                      (unsigned long)t0.pixelFormat);
            }

            CFTimeInterval presTime = cp_time_to_cf_time_interval(
                cp_frame_timing_get_presentation_time(cp_drawable_get_frame_timing(drawable)));
            ar_device_anchor_t anchor = ar_device_anchor_create();
            ar_device_anchor_query_status_t anchorStatus =
                ar_world_tracking_provider_query_device_anchor_at_timestamp(wtp, presTime, anchor);
            cp_drawable_set_device_anchor(drawable, anchor);

            if (!dusk3d_haveScreenAnchor && anchorStatus == ar_device_anchor_query_status_success &&
                dusk3d_frameCount > 30) {
                dusk3d_frozenHead = ar_device_anchor_get_origin_from_anchor_transform(anchor);
                dusk3d_haveScreenAnchor = true;
                NSLog(@"[Dusk3D] screen anchored at head (%.2f,%.2f,%.2f)", dusk3d_frozenHead.columns[3].x,
                      dusk3d_frozenHead.columns[3].y, dusk3d_frozenHead.columns[3].z);
            }

            id<MTLCommandBuffer> command_buffer = [queue commandBuffer];

            // Copy both per-eye engine textures into mipmapped sampling copies (on
            // THIS queue, so copy + sample are coherent). Falls back to the test
            // pattern until the engine's eye framebuffers exist (M3).
            id<MTLTexture> monoTex = nil;
            for (int e = 0; e < 2; e++) {
                IOSurfaceRef ios = (IOSurfaceRef)Dusk3D_GetEyeIOSurface(e + 1);
                if (!ios || gDusk3DEyeW <= 0 || gDusk3DEyeH <= 0)
                    continue;
                // Wrap the engine's eye IOSurface as an MTLTexture on THIS device
                // (cached by identity). This is the Q-004 interop: Dawn wrote the game
                // frame into the IOSurface; Metal reads it here.
                if (dusk3d_eyeSrcSurface[e] != ios || dusk3d_eyeSrcTex[e] == nil) {
                    MTLTextureDescriptor* sd = [MTLTextureDescriptor
                        texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                     width:(NSUInteger)gDusk3DEyeW
                                                    height:(NSUInteger)gDusk3DEyeH
                                                 mipmapped:NO];
                    sd.usage = MTLTextureUsageShaderRead;
                    dusk3d_eyeSrcTex[e] = [command_buffer.device newTextureWithDescriptor:sd
                                                                                iosurface:ios
                                                                                    plane:0];
                    dusk3d_eyeSrcSurface[e] = ios;
                }
                id<MTLTexture> src = dusk3d_eyeSrcTex[e];
                if (!src)
                    continue;
                monoTex = src;
                if (dusk3d_eyeCopy[e] == nil || dusk3d_eyeCopy[e].width != src.width ||
                    dusk3d_eyeCopy[e].height != src.height || dusk3d_eyeCopy[e].pixelFormat != src.pixelFormat) {
                    MTLTextureDescriptor* td =
                        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:src.pixelFormat
                                                                           width:src.width
                                                                          height:src.height
                                                                       mipmapped:YES];
                    td.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
                    td.storageMode = MTLStorageModePrivate;
                    dusk3d_eyeCopy[e] = [src.device newTextureWithDescriptor:td];
                }
                id<MTLBlitCommandEncoder> blit = [command_buffer blitCommandEncoder];
                [blit copyFromTexture:src toTexture:dusk3d_eyeCopy[e]];
                if (dusk3d_eyeCopy[e].mipmapLevelCount > 1)
                    [blit generateMipmapsForTexture:dusk3d_eyeCopy[e]];
                [blit endEncoding];
            }
            if (monoTex == nil)
                monoTex = dusk3d_testPattern;

            size_t views = cp_drawable_get_view_count(drawable);
            // Foveation (VISIONOS-FOVEATION-GUIDE): per-view targeting must come from each view's
            // texture map (texIdx/slice/viewport), never hardcoded texture 0 -- with the dedicated
            // layout each eye has its OWN color/depth texture AND its own rasterization rate map.
            size_t rateMapCount = cp_drawable_get_rasterization_rate_map_count(drawable);
            id<MTLTexture> color0 = cp_drawable_get_color_texture(drawable, 0); // format probe only

            simd_float4x4 placement = dusk3d_haveScreenAnchor ? dusk3d_make_screen_anchor(dusk3d_frozenHead)
                                                              : dusk3d_translate(0.0f, 0.0f, -dusk3d_screenDist);
            // Width/Height are FREE (ultra-wide/skinny like 2D allowed). The default
            // panel aspect (~3:2) matches the render so it's undistorted out of the box.
            simd_float4x4 model = simd_mul(placement, dusk3d_scale(dusk3d_screenHalfW, dusk3d_screenHalfH, 1.0f));
            simd_float4x4 originFromDevice = ar_device_anchor_get_origin_from_anchor_transform(anchor);

            if (dusk3d_haveScreenAnchor && dusk3d_frameCount > 60)
                dusk3d_log_fidelity(drawable, monoTex);

            float srgbDecode = 0.0f;
            {
                MTLPixelFormat sf = monoTex.pixelFormat, df = color0.pixelFormat;
                BOOL srcEncoded = (sf == MTLPixelFormatBGRA8Unorm || sf == MTLPixelFormatRGBA8Unorm);
                BOOL dstLinear = (df == MTLPixelFormatBGRA8Unorm_sRGB || df == MTLPixelFormatRGBA8Unorm_sRGB ||
                                  df == MTLPixelFormatRGBA16Float);
                srgbDecode = (srcEncoded && dstLinear) ? 1.0f : 0.0f;
            }

            for (size_t v = 0; v < views; v++) {
                // Per-view targeting straight from the view's texture map -- foveation-correct
                // for BOTH layouts (dedicated: per-eye texIdx, slice 0; layered: texIdx 0, slice v).
                cp_view_t view = cp_drawable_get_view(drawable, v);
                cp_view_texture_map_t tmap = cp_view_get_view_texture_map(view);
                size_t texIdx = cp_view_texture_map_get_texture_index(tmap);
                size_t slice = cp_view_texture_map_get_slice_index(tmap);
                MTLViewport vp = cp_view_texture_map_get_viewport(tmap);
                id<MTLTexture> color = cp_drawable_get_color_texture(drawable, texIdx);
                id<MTLTexture> depth = cp_drawable_get_depth_texture(drawable, texIdx);

                MTLRenderPassDescriptor* pass = [MTLRenderPassDescriptor renderPassDescriptor];
                pass.colorAttachments[0].texture = color;
                pass.colorAttachments[0].slice = slice;
                pass.colorAttachments[0].loadAction = MTLLoadActionClear;
                pass.colorAttachments[0].storeAction = MTLStoreActionStore;
                pass.colorAttachments[0].clearColor = MTLClearColorMake(0.0, 0.0, 0.0, 0.0);
                if (depth) {
                    pass.depthAttachment.texture = depth;
                    pass.depthAttachment.slice = slice;
                    pass.depthAttachment.loadAction = MTLLoadActionClear;
                    pass.depthAttachment.storeAction = MTLStoreActionStore;
                    pass.depthAttachment.clearDepth = 1.0;
                }
                // Attach this eye's rasterization rate map (nil / count 0 when foveation is off).
                if (rateMapCount > 0) {
                    pass.rasterizationRateMap =
                        cp_drawable_get_rasterization_rate_map(drawable, texIdx < rateMapCount ? texIdx : 0);
                }
                // This eye's texture: its own stereo image if ready, else mono.
                size_t srcEye = v;
                // DEBUG (sim gate): DUSK_VP3D_SHOWEYE forces every view to sample a
                // specific eye so the mono sim can prove the RIGHT eye is non-black.
                static int forceEye = -2;
                if (forceEye == -2) {
                    const char* fe = getenv("DUSK_VP3D_SHOWEYE");
                    forceEye = fe ? atoi(fe) : -1;
                }
                if (forceEye >= 0) srcEye = (size_t)forceEye;
                id<MTLTexture> tex = (srcEye < 2 && dusk3d_eyeCopy[srcEye]) ? dusk3d_eyeCopy[srcEye] : monoTex;

                id<MTLRenderCommandEncoder> enc = [command_buffer renderCommandEncoderWithDescriptor:pass];
                [enc setViewport:vp]; // the view's (foveation-aware) viewport, required before draws
                float dimNow = dusk3d_dimLevel;
                if (dimNow > 0.003f && dusk3d_dimPipeline) {
                    [enc setRenderPipelineState:dusk3d_dimPipeline];
                    [enc setDepthStencilState:dusk3d_dimDepthState];
                    [enc setFragmentBytes:&dimNow length:sizeof(dimNow) atIndex:0];
                    [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                }
                if (tex && dusk3d_pipeline) {
                    simd_float4x4 deviceFromEye = cp_view_get_transform(view);
                    simd_float4x4 eyeFromOrigin = simd_inverse(simd_mul(originFromDevice, deviceFromEye));
                    simd_float4x4 proj = matrix_identity_float4x4;
                    if (__builtin_available(visionOS 2.0, *))
                        proj = cp_drawable_compute_projection(drawable,
                                                              cp_axis_direction_convention_right_up_back, v);
                    simd_float4x4 mvp = simd_mul(proj, simd_mul(eyeFromOrigin, model));

                    [enc setRenderPipelineState:dusk3d_pipeline];
                    [enc setDepthStencilState:dusk3d_depthState];
                    [enc setVertexBytes:&mvp length:sizeof(mvp) atIndex:0];
                    [enc setFragmentBytes:&srgbDecode length:sizeof(srgbDecode) atIndex:0];
                    [enc setFragmentTexture:tex atIndex:0];
                    [enc drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:4];
                }
                [enc endEncoding];
            }

            cp_drawable_encode_present(drawable, command_buffer);
            [command_buffer commit];

            dusk3d_frameCount++;
            if (dusk3d_frameCount == 3 || (dusk3d_frameCount % 600) == 0)
                NSLog(@"[Dusk3D] frame %d -- source %lux%lu eyeL=%d eyeR=%d framesL=%d framesR=%d srgbDecode=%.0f",
                      dusk3d_frameCount, (unsigned long)monoTex.width, (unsigned long)monoTex.height,
                      (int)(dusk3d_eyeCopy[0] != nil), (int)(dusk3d_eyeCopy[1] != nil), Dusk3D_GetEyeFrames(1),
                      Dusk3D_GetEyeFrames(2), srgbDecode);

            cp_frame_end_submission(frame);
        }
    }

    dusk3d_eyeCopy[0] = dusk3d_eyeCopy[1] = nil;
    dusk3d_testPattern = nil;
    if (notifyEnded)
        Dusk3D_Immersive_Ended();
    gDusk3DRunning = 0; // signal the shell LAST, after cleanup
}
