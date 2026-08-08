// MMD-style Shader for Maya Viewport 2.0 (DirectX 11)
// Based on MikuMikuDance rendering style with Maya integration
// Version: 2.0

#define NumberOfMipMaps 0
#define PI 3.1415926

//--------------------------------------------------------------------------------------
// sRGB helper
//--------------------------------------------------------------------------------------
// MMD performs all texture/lighting math in gamma (sRGB) space, and dx11Shader
// feeds the effect raw gamma texels, so the lighting below is already MMD-correct
// in gamma space. The only mismatch is the OUTPUT: with Color Management on, the
// view transform applies an extra linear->sRGB encode to our (already gamma-space)
// color, double-gamma-ing it into a washed-out look. Decoding the final color to
// linear here cancels that encode exactly, restoring the MMD look (and matching
// the CM-off reference). Under CM-off (no view transform) this would darken the
// result, so the importer steers the viewport to a CM-on sRGB/Un-tone-mapped view.
float3 SrgbToLinear(float3 c)
{
    c = max(c, 0.0);
    float3 lo = c / 12.92;
    float3 hi = pow((c + 0.055) / 1.055, 2.4);
    return lerp(hi, lo, step(c, 0.04045));
}

//--------------------------------------------------------------------------------------
// Samplers
//--------------------------------------------------------------------------------------
SamplerState LinearSampler : register(s0)
{
    Filter = MIN_MAG_MIP_LINEAR;
    AddressU = Wrap;
    AddressV = Wrap;
};

SamplerState ToonSampler : register(s1)
{
    Filter = MIN_MAG_MIP_LINEAR;
    AddressU = Clamp;
    AddressV = Clamp;
};

SamplerState ShadowSampler : register(s2)
{
    Filter = MIN_MAG_MIP_POINT;
    AddressU = Border;
    AddressV = Border;
    BorderColor = float4(1.0f, 1.0f, 1.0f, 1.0f);
};

//--------------------------------------------------------------------------------------
// Textures
//--------------------------------------------------------------------------------------
Texture2D MainTexture<
    string UIGroup = "Textures";
    string ResourceName = "";
    string UIWidget = "FilePicker";
    string UIName = "Main Texture";
    string ResourceType = "2D";
    int mipmaplevels = NumberOfMipMaps;
    int UIOrder = 100;
>;

Texture2D SphereTexture<
    string UIGroup = "Textures";
    string ResourceName = "";
    string UIWidget = "FilePicker";
    string UIName = "Sphere Texture";
    string ResourceType = "2D";
    int mipmaplevels = NumberOfMipMaps;
    int UIOrder = 101;
>;

Texture2D ToonTexture<
    string UIGroup = "Textures";
    string ResourceName = "";
    string UIWidget = "FilePicker";
    string UIName = "Toon Texture";
    string ResourceType = "2D";
    int mipmaplevels = NumberOfMipMaps;
    int UIOrder = 102;
>;

// Shadow maps
Texture2D Light0ShadowMap : SHADOWMAP
<
    string Object = "Light 0";
    string UIWidget = "None";
    int UIOrder = 1000;
>;

// Same-frame native receiver probe.  The C++ render override assigns its
// private R32F caster target to borrowed body MShaderInstances using
// MRenderTargetAssignment.  It is intentionally opt-in and encodes the raw
// sampled depth directly in BODY pixels; this is a binding/ordering witness,
// not a shadow compare, PCF, bias, or production receiver-composition path.
Texture2D NativeCasterDepthTexture<
    string UIGroup = "Native Diagnostics";
    string UIName = "Native Caster Depth Probe";
    string UIWidget = "None";
    string ResourceType = "2D";
    int mipmaplevels = NumberOfMipMaps;
>;

int NativeCasterProbe<
    string UIGroup = "Native Diagnostics";
    string UIName = "Native Caster Depth Probe Enabled";
    string UIWidget = "None";
    float UIMin = 0;
    float UIMax = 1;
    float UIStep = 1;
> = 0;

//--------------------------------------------------------------------------------------
// Constant Buffers
//--------------------------------------------------------------------------------------

// Per-frame parameters. Maya's dx11Shader uniform builder binds global effect
// variables more reliably than explicit cbuffers.
float4x4 View       : View<string UIWidget = "None";>;
float4x4 ViewInv    : ViewInverse<string UIWidget = "None";>;
float4x4 Projection : Projection<string UIWidget = "None";>;
float4x4 ViewProjection : ViewProjection<string UIWidget = "None";>;
float3 ViewPosition : ViewPosition<string UIWidget = "None";>;
float2 ScreenSize : ViewportPixelSize<string UIWidget = "None";>;
float DevicePixelRatio< string UIWidget = "None"; > = 1.0f;

// Per-object parameters
float4x4 World               : World<string UIWidget = "None";>;
float4x4 WorldInverse        : WorldInverse<string UIWidget = "None";>;
float4x4 WorldInverseTranspose : WorldInverseTranspose<string UIWidget = "None";>;
float4x4 WorldViewProjection : WorldViewProjection<string UIWidget = "None";>;

// Material parameters
float3 DiffuseColorRGB<
    string UIGroup = "Material";
    string UIName = "Diffuse Color";
    string UIWidget = "Color";
    int UIOrder = 200;
> = {0.8f, 0.8f, 0.8f};

float DiffuseColorA<
    string UIGroup = "Material";
    string UIName = "Diffuse Alpha";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 1.0;
    float UIStep = 0.001;
    int UIOrder = 201;
> = 1.0f;

float4 MainTextureMultiply = {1.0f, 1.0f, 1.0f, 1.0f};
float4 MainTextureAdd = {0.0f, 0.0f, 0.0f, 0.0f};
float4 SphereTextureMultiply = {1.0f, 1.0f, 1.0f, 1.0f};
float4 SphereTextureAdd = {0.0f, 0.0f, 0.0f, 0.0f};
float4 ToonTextureMultiply = {1.0f, 1.0f, 1.0f, 1.0f};
float4 ToonTextureAdd = {0.0f, 0.0f, 0.0f, 0.0f};

float3 SpecularColor<
    string UIGroup = "Material";
    string UIName = "Specular Color";
    string UIWidget = "Color";
    int UIOrder = 202;
> = {0.5f, 0.5f, 0.5f};

float Shininess<
    string UIGroup = "Material";
    string UIName = "Shininess";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 100.0;
    int UIOrder = 203;
> = 20.0f;

float3 AmbientColor<
    string UIGroup = "Material";
    string UIName = "Ambient Color";
    string UIWidget = "Color";
    int UIOrder = 204;
> = {0.3f, 0.3f, 0.3f};

// The generated MMD ramp is bright at V=0 and dark at V=1.  Maya's file
// texture path samples the authored top-origin ramp with a small calibrated
// offset; keep that calibration explicit instead of hiding it in N.L math.
float ToonCoordinateOffset<
    string UIGroup = "Lighting";
    string UIName = "Toon Coordinate Offset";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 1.0;
    float UIStep = 0.001;
    int UIOrder = 205;
> = 0.55f;

// Transparency
float Opacity : OPACITY<
    string UIGroup = "Transparency";
    string UIName = "Opacity";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 1.0;
    float UIStep = 0.001;
    int UIOrder = 300;
> = 1.0f;

// Edge (Outline) parameters
float3 EdgeColorRGB<
    string UIGroup = "Outline";
    string UIName = "Edge Color";
    string UIWidget = "Color";
    int UIOrder = 400;
> = {0.0f, 0.0f, 0.0f};

float EdgeColorA<
    string UIGroup = "Outline";
    string UIName = "Edge Alpha";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 1.0;
    float UIStep = 0.001;
    int UIOrder = 401;
> = 1.0f;

float EdgeSize<
    string UIGroup = "Outline";
    string UIName = "Edge Size";
    string UIWidget = "Slider";
    float UIMin = 0.0f;
    float UIMax = 10.0f;
    int UIOrder = 402;
> = 1.0f;

// Sphere mapping
int SphereMode<
    string UIGroup = "Effects";
    string UIName = "Sphere Mode";
    string UIFieldNames = "None:Multiply:Add:SubTexture";
    float UIMin = 0;
    float UIMax = 3;
    float UIStep = 1;
    int UIOrder = 500;
> = 0;

int HasMainTexture<
    string UIGroup = "Textures";
    string UIName = "Has Main Texture";
    float UIMin = 0;
    float UIMax = 1;
    float UIStep = 1;
    int UIOrder = 510;
> = 0;

int HasSphereTexture<
    string UIGroup = "Textures";
    string UIName = "Has Sphere Texture";
    float UIMin = 0;
    float UIMax = 1;
    float UIStep = 1;
    int UIOrder = 511;
> = 0;

int HasToonTexture<
    string UIGroup = "Textures";
    string UIName = "Has Toon Texture";
    float UIMin = 0;
    float UIMax = 1;
    float UIStep = 1;
    int UIOrder = 512;
> = 0;

// Native VP2 material items can opt into an sRGB framebuffer path so legacy
// MMD alpha blending is performed in the authored color space.  Product
// dx11Shader materials keep the default zero and use the shared CM-on path.
int NativeSrgbOutput<
    string UIWidget = "None";
> = 0;

// Native caster capability spike.  The C++ MRenderOverride binds a fixed
// orthographic caster matrix before the selected mmdRenderShape items draw.
// Explicit row-major storage matches Maya's MPoint * MMatrix convention and
// the row-vector mul() calls below; this is not a production scene-light
// projection or receiver-composition path.
row_major float4x4 CasterLightViewProjection<
    string UIWidget = "None";
>;

float CasterDepthBias<
    string UIWidget = "None";
> = 0.35f;

// Shadow parameters
bool UseShadows<
    string UIGroup = "Lighting";
    string UIName = "Enable Shadows";
    int UIOrder = 600;
> = false;

float ShadowStrength<
    string UIGroup = "Lighting";
    string UIName = "Shadow Strength";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 1.0;
    int UIOrder = 601;
> = 1.0f;

float ShadowBias : ShadowMapBias<
    string UIGroup = "Lighting";
    string UIName = "Shadow Bias";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UISoftMax = 10.0;
    float UIStep = 0.001;
    int UIOrder = 602;
> = 0.01f;

// MMD light. MMD has exactly one global directional light. It is driven by the
// `mmd_light` controller null (worldMatrix -> direction, mmd_light_color -> color)
// and is the ONLY light this shader reacts to: Maya's automatic scene-light
// binding (DIRECTION / LIGHTCOLOR semantics) is intentionally not used, so the
// look does not depend on the viewport lighting mode ("Use Default/All Lights").
float3 MMDLightDirection<
    string UIGroup = "Lighting";
    string UIName = "MMD Light Direction";
> = {-0.5f, -1.0f, -1.0f};

float3 MMDLightColor<
    string UIGroup = "Lighting";
    string UIName = "MMD Light Color";
> = {0.6039216f, 0.6039216f, 0.6039216f};

float4x4 Light0Matrix : SHADOWMAPMATRIX
<
    string Object = "Light 0";
    string UIWidget = "None";
    int UIOrder = 1000;
>;

//--------------------------------------------------------------------------------------
// Vertex Shader Input/Output Structures
//--------------------------------------------------------------------------------------
struct VS_INPUT
{
    float3 position     : POSITION;
    float2 texCoord0    : TEXCOORD0;
    float2 texCoord1    : TEXCOORD1;
    float4 vertexColor0 : COLOR0;
    float4 vertexColor1 : COLOR1;
    float3 normal       : NORMAL;
    float3 tangent      : TANGENT;
    float3 binormal     : BINORMAL;
};

struct VS_OUTPUT
{
    float4 position     : SV_POSITION;
    float2 texCoord0    : TEXCOORD0;
    float2 texCoord1    : TEXCOORD1;
    float4 vertexColor0 : TEXCOORD2;
    float4 vertexColor1 : TEXCOORD3;
    float3 worldPosition : TEXCOORD4;
    float4 shadowCoord  : TEXCOORD5;
    float3 worldNormal  : NORMAL;
};

//--------------------------------------------------------------------------------------
// Shadow calculation
//--------------------------------------------------------------------------------------
float CalculateShadow(float4 shadowCoord, Texture2D shadowMap)
{
    if (!UseShadows)
        return 1.0f;

    // Transform to shadow map space
    float3 shadowPos = shadowCoord.xyz / shadowCoord.w;

    // Check if position is in shadow map bounds
    if (shadowPos.x < -1.0f || shadowPos.x > 1.0f ||
        shadowPos.y < -1.0f || shadowPos.y > 1.0f ||
        shadowPos.z < 0.0f || shadowPos.z > 1.0f)
        return 1.0f;

    // Convert to texture coordinates
    float2 shadowUV = shadowPos.xy * 0.5f + 0.5f;
    shadowUV.y = 1.0f - shadowUV.y; // Flip Y for Maya

    // Sample shadow map
    float shadowDepth = shadowMap.Sample(ShadowSampler, shadowUV).r;
    float currentDepth = shadowPos.z - ShadowBias / shadowCoord.w;

    // Calculate shadow
    float shadow = (currentDepth > shadowDepth) ? 0.0f : 1.0f;

    return lerp(1.0f, shadow, ShadowStrength);
}

//--------------------------------------------------------------------------------------
// Main Pass Vertex Shader
//--------------------------------------------------------------------------------------
VS_OUTPUT MainVS(VS_INPUT input)
{
    VS_OUTPUT output = (VS_OUTPUT)0;

    // Transform position
    float4 localPos = float4(input.position, 1.0);
    float4 worldPos = mul(localPos, World);
    output.position = mul(localPos, WorldViewProjection);
    output.worldPosition = worldPos.xyz;

    // Transform normal
    output.worldNormal = normalize(mul(input.normal, (float3x3)WorldInverseTranspose));

    // Pass through UV
    output.texCoord0 = float2(input.texCoord0.x, 1.0 - input.texCoord0.y); // Flip V for Maya
    output.texCoord1 = input.texCoord1;
    output.vertexColor0 = input.vertexColor0;
    output.vertexColor1 = input.vertexColor1;

    // Calculate shadow coordinates
    output.shadowCoord = mul(worldPos, Light0Matrix);

    return output;
}

// The caster deliberately keeps the same vertex input contract as the native
// material shader so MSceneRender can reuse mmdRenderShape's geometry buffers.
VS_OUTPUT CasterVS(VS_INPUT input)
{
    VS_OUTPUT output = (VS_OUTPUT)0;
    float4 localPos = float4(input.position, 1.0);
    float4 worldPos = mul(localPos, World);
    output.position = mul(worldPos, CasterLightViewProjection);
    // A/B/A validation changes only clip Z.  XY and therefore raster
    // footprint remain invariant while the strict LESS D32 test continues
    // to select the same front-most fragments.
    output.position.z += CasterDepthBias * output.position.w;
    output.worldPosition = worldPos.xyz;
    output.worldNormal = input.normal;
    output.texCoord0 = input.texCoord0;
    output.texCoord1 = input.texCoord1;
    output.vertexColor0 = input.vertexColor0;
    output.vertexColor1 = input.vertexColor1;
    return output;
}

float CasterPS(VS_OUTPUT input) : SV_TARGET
{
    // SV_POSITION.z is already post-divide [0, 1] depth for this finite
    // orthographic matrix.  Return it directly to the R32F target; do not
    // use SV_Depth, divide by w, saturate, or substitute an occupancy value.
    return input.position.z;
}

//--------------------------------------------------------------------------------------
// Main Pass Pixel Shader
//--------------------------------------------------------------------------------------
float4 MainPS(VS_OUTPUT input) : SV_TARGET
{
    // Normalize inputs
    float3 normal = normalize(input.worldNormal);
    float3 viewDir = normalize(ViewPosition - input.worldPosition);
    // The imported PMX normals already carry the Maya Z-mirror and the mesh
    // winding is reversed during conversion. Keep those authored normals for
    // the dot product; a view-facing flip would change corner normals on the
    // visible edge bands and is not equivalent to Three's primitive-facing
    // DoubleSide handling.
    // MMDLightDirection is the direction the light travels (light's world -Z);
    // negate to get the surface -> light vector used by the lighting model.
    float3 lightDir = -normalize(MMDLightDirection);
    float3 lightColor = MMDLightColor;

    // Sample textures. dx11Shader feeds the effect raw gamma (sRGB) texels even
    // under CM-on, which is the space MMD's lighting math expects, so sample as-is.
    float4 texColor = float4(1.0, 1.0, 1.0, 1.0);
    if (HasMainTexture != 0)
    {
        texColor = MainTexture.Sample(LinearSampler, input.texCoord0);
        texColor = texColor * MainTextureMultiply + MainTextureAdd;
    }

    // Calculate shadow
    float shadow = CalculateShadow(input.shadowCoord, Light0ShadowMap);

    // Three uploads toon maps with flipY=true. Maya's file texture sampling is
    // top-origin, so convert the same signed coordinate back to the authored
    // image row while retaining Three's positive-lighting direction.
    // Preserve the linearly interpolated world normal for the toon-ramp
    // coordinate. Normalizing here per pixel changes the corner-normal
    // interpolation that the reference MMD shader uses; the normalized
    // `normal` above remains the specular/lighting normal.
    float NdotL = dot(input.worldNormal, lightDir);
    float toonV = saturate(ToonCoordinateOffset - NdotL * 0.5);
    float3 toonColor = float3(1.0, 1.0, 1.0);
    if (HasToonTexture != 0)
    {
        // The MMD contract samples the first column of the vertical ramp.
        float4 toonSample = ToonTexture.Sample(ToonSampler, float2(0.0, toonV));
        float4 factoredToon = toonSample * ToonTextureMultiply + ToonTextureAdd;
        toonColor = factoredToon.rgb;
    }
    // MMD's material base is authored diffuse * light + ambient.  N.L selects
    // the toon ramp when present; it must not become an extra Lambert
    // multiplier on the native material base.
    float3 materialBase = saturate(DiffuseColorRGB * lightColor + AmbientColor) * texColor.rgb;

    // Sphere mapping
    float3 sphereColor = float3(1.0, 1.0, 1.0);
    if (SphereMode > 0 && HasSphereTexture != 0)
    {
        // Three's WebGPU MMD path normalizes the interpolated view normal for
        // lighting, but deliberately uses the raw interpolated normal for the
        // sphere UV.  Re-normalizing here expands the UV radius on beveled
        // faces and makes the sphere map fade too aggressively at the edges.
        float3 sphereNormal = mul(float4(input.worldNormal, 0.0), View).xyz;
        float2 sphereUV;
        sphereUV.x = sphereNormal.x * 0.5 + 0.5;
        sphereUV.y = sphereNormal.y * 0.5 + 0.5;
        float4 sphereSample = SphereTexture.Sample(LinearSampler, sphereUV);
        float4 factoredSphere = sphereSample * SphereTextureMultiply + SphereTextureAdd;
        sphereColor = factoredSphere.rgb;
    }

    // FullShader order: base/texture -> sphere -> toon -> specular. Applying
    // sphere after specular incorrectly tints highlights in multiply mode.
    float3 surfaceColor = materialBase;
    if (SphereMode == 1 && HasSphereTexture != 0) // Multiply
        surfaceColor *= sphereColor;
    else if (SphereMode == 2 && HasSphereTexture != 0) // Add
        surfaceColor += sphereColor;
    if (HasToonTexture != 0)
        surfaceColor *= toonColor;

    // Projected shadows remain a separate Maya viewport factor.
    float3 diffuse = surfaceColor * shadow;

    // MMD skips specular completely when the authored power is non-positive.
    // Positive powers use the Blinn-Phong half vector without an extra N.L gate.
    float3 specular = float3(0.0, 0.0, 0.0);
    if (Shininess > 0.0)
    {
        float3 halfVec = normalize(lightDir + viewDir);
        float NdotH = saturate(dot(normal, halfVec));
        float specFactor = pow(NdotH, Shininess);
        specular = SpecularColor * specFactor * lightColor * shadow;
    }

    float3 litColor = diffuse + specular;

    // Keep the normal product path byte-stable when the default-off probe is
    // disabled.  When enabled, sample only the exact same-frame caster target
    // and visibly encode its raw depth in body pixels for an A/B/A witness.
    if (NativeCasterProbe != 0)
    {
        float4 casterClip = mul(float4(input.worldPosition, 1.0),
                                CasterLightViewProjection);
        float casterW = max(abs(casterClip.w), 1.0e-6);
        float2 casterUV = casterClip.xy / casterW * 0.5 + 0.5;
        float sampledDepth = NativeCasterDepthTexture.Sample(
            ShadowSampler, saturate(casterUV)).r;
        litColor = float3(sampledDepth, 1.0 - sampledDepth, 0.0);
    }

    // Apply opacity
    float opacity = texColor.a * DiffuseColorA * Opacity;

    // MMD parity (mmd-shading-notes §8): discard fully transparent fragments.
    // This is essential now that transparent materials write depth -- without
    // it, alpha==0 texels of cutout textures (hair, ribbons) would still write
    // depth and punch black holes / halos into whatever is behind them.
    clip(opacity - 0.003);

    // Product materials decode the gamma-space MMD result to linear; the view
    // transform re-encodes it to sRGB under CM-on.  Native VP2 items can use a
    // CM-off sRGB target so legacy MMD alpha blending remains in gamma space.
    float3 outputColor = NativeSrgbOutput != 0 ? litColor : SrgbToLinear(litColor);
    return float4(outputColor, opacity);
}

//--------------------------------------------------------------------------------------
// Edge Pass Vertex Shader
//--------------------------------------------------------------------------------------
VS_OUTPUT EdgeVS(VS_INPUT input)
{
    VS_OUTPUT output = (VS_OUTPUT)0;

    float4 localPos = float4(input.position, 1.0);
    float4 worldPos = mul(localPos, World);
    float4 clipPos = mul(localPos, WorldViewProjection);

    float3 worldNormal = normalize(mul(input.normal, (float3x3)WorldInverseTranspose));
    float3 viewNormal = normalize(mul(worldNormal, (float3x3)View));
    float2 screenNormal = viewNormal.xy;
    screenNormal /= max(length(screenNormal), 1.0e-5);

    float2 safeScreenSize = max(ScreenSize, float2(1.0, 1.0));
    // ViewportPixelSize is physical; authored EdgeSize is in logical pixels.
    // The 0.40 factor is the calibrated expansion for the 1024px fixtures:
    // it restores the authored silhouette width without the interior bleed
    // caused by the much larger historical 0.25 experiment.
    float logicalEdgeSize = EdgeSize * max(DevicePixelRatio, 1.0e-5);
    clipPos.xy += screenNormal / (safeScreenSize * 0.40) * logicalEdgeSize * clipPos.w;

    output.position = clipPos;
    output.worldPosition = worldPos.xyz;

    return output;
}

// A translucent body writes depth before its outline is evaluated.  Keep the
// inverted hull slightly behind that body so the strict-less edge test leaves
// only the exterior silhouette instead of bleeding through translucent color.
VS_OUTPUT EdgeVSTranslucent(VS_INPUT input)
{
    VS_OUTPUT output = EdgeVS(input);
    output.position.z += 1.0e-2 * output.position.w;
    return output;
}

//--------------------------------------------------------------------------------------
// Edge Pass Pixel Shader
//--------------------------------------------------------------------------------------
float4 EdgePS(VS_OUTPUT input) : SV_TARGET
{
    // All techniques contain the edge pass. A material opts out in shader
    // space by setting EdgeSize to zero, avoiding separate NoEdge techniques.
    clip(EdgeSize - 1.0e-5);
    // EdgeColorRGB is an authored gamma-space color.  Product items decode to
    // linear for Maya's CM-on view transform; native items render into the
    // CM-off sRGB target and therefore keep the authored value directly.
    float3 outputColor = NativeSrgbOutput != 0
                             ? EdgeColorRGB
                             : SrgbToLinear(EdgeColorRGB);
    return float4(outputColor, EdgeColorA);
}

//--------------------------------------------------------------------------------------
// Rasterizer States
//--------------------------------------------------------------------------------------
RasterizerState CullFront
{
    CullMode = Front;
};

RasterizerState CullBack
{
    CullMode = Back;
};

RasterizerState CullNone
{
    CullMode = None;
};

//--------------------------------------------------------------------------------------
// Blend States
//--------------------------------------------------------------------------------------
BlendState AlphaBlend
{
    BlendEnable[0] = TRUE;
    SrcBlend = SRC_ALPHA;
    DestBlend = INV_SRC_ALPHA;
    BlendOp = ADD;
    SrcBlendAlpha = ONE;
    DestBlendAlpha = INV_SRC_ALPHA;
    BlendOpAlpha = ADD;
    RenderTargetWriteMask[0] = 0x0F;
};

BlendState NoBlend
{
    BlendEnable[0] = FALSE;
};

//--------------------------------------------------------------------------------------
// Depth Stencil States
//--------------------------------------------------------------------------------------
// Body surfaces use strict-less depth testing and write depth, including
// alpha-blended MMD materials. Fully transparent fragments are discarded in
// MainPS; every surviving fragment writes depth.
DepthStencilState EnableDepth
{
    DepthEnable = TRUE;
    DepthWriteMask = ALL;
    DepthFunc = LESS;
};

// The inverted-hull edge tests depth but must not alter the surface depth.
DepthStencilState EdgeDepthReadOnly
{
    DepthEnable = TRUE;
    DepthWriteMask = ZERO;
    DepthFunc = LESS;
};

//--------------------------------------------------------------------------------------
// Techniques
//--------------------------------------------------------------------------------------

// Single-sided material: inverted-hull edge plus back-face-culling main pass.
technique11 MMDTechnique<
    int isTransparent = 0;
>
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

// Double-sided material: the same edge pass plus a cull-none main pass.
technique11 MMDTechniqueDoubleSided<
    int isTransparent = 0;
>
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

// Blended materials use the same read-only edge pass and an independent
// alpha-blended main pass. The native MMD queue owns material/submesh order;
// the body still writes depth so that strict-less testing preserves that
// order's natural translucent layer selection.
technique11 MMDTechniqueTranslucent<
    int isTransparent = 1;
>
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullFront);
        SetBlendState(AlphaBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVSTranslucent()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
}

technique11 MMDTechniqueTranslucentDoubleSided<
    int isTransparent = 1;
>
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(AlphaBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVSTranslucent()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
}

// Native VP2 render items use one effect instance per material/submesh.  Keep
// these single-pass techniques separate from the product shader's explicit
// edge+body pass sequence so MPxGeometryOverride can bind body and outline
// states to independent VP2 render items.
technique11 MMDNativeOpaque
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

technique11 MMDNativeTranslucent
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullFront);
        SetBlendState(AlphaBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

technique11 MMDNativeOpaqueDoubleSided
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

technique11 MMDNativeTranslucentDoubleSided
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(AlphaBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

technique11 MMDNativeOutline
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
}

// Double-sided materials need the hull on both winding directions.  This is
// especially important for a cutout plane: the body discards alpha==0 texels,
// so the edge must still fill those holes instead of being culled with the
// plane's camera-facing winding.
technique11 MMDNativeOutlineDoubleSided
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullBack);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
}

technique11 MMDNativeOutlineTranslucent
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVSTranslucent()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
}

technique11 MMDNativeOutlineTranslucentDoubleSided
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVSTranslucent()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullBack);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EdgeDepthReadOnly, 0);
    }
}

// Opt-in native caster pass used only by MmdNativeCasterRenderOverride.  It
// writes rasterized clip depth to an R32F target and depth-tests the selected
// mmdRenderShape geometry with the fixed row-vector caster matrix.
technique11 MMDNativeCaster
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, CasterVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, CasterPS()));
        SetRasterizerState(CullNone);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}
