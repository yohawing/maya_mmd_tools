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

// Kept for shader-uniform ABI compatibility with existing Maya material
// nodes.  The reference shader uses 0.5 - N.L * 0.5 directly; this legacy
// control is intentionally not part of the ramp calculation.
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
    float4 baseColor    : TEXCOORD6;
    float3 eye          : TEXCOORD7;
    float2 sphereUV     : TEXCOORD8;
    float3 specular     : TEXCOORD9;
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

    // The Maya world/inverse-transpose adapter remains the source of the
    // vertex normal.  The reference shader normalizes this value per vertex;
    // the pixel shaders deliberately consume the interpolated raw value for
    // toon and self-shadow decisions.
    output.worldNormal = normalize(mul(input.normal, (float3x3)WorldInverseTranspose));

    // Reference Basic_VS/BufferShadow_VS values mapped to Maya's inputs.
    // AmbientColor is the imported PMX ambient term here.  The reference
    // AmbientColor/EgColor slots are host-composed to this flat PMX base so a
    // no-toon material has no additional N.L diffuse term.
    output.baseColor = float4(
        saturate(DiffuseColorRGB * MMDLightColor + AmbientColor),
        DiffuseColorA);
    output.eye = mul(float4(0.0f, 0.0f, 0.0f, 1.0f), ViewInv).xyz
                 - worldPos.xyz;

    // Reference sphere coordinates use the normalized vertex normal in view
    // space and the authored top-origin Y direction.  Main texture UV keeps
    // Maya's existing V flip below.
    float3 normalWV = mul(output.worldNormal, (float3x3)View);
    output.sphereUV = float2(
        normalWV.x * 0.5f + 0.5f,
        normalWV.y * 0.5f + 0.5f);

    // The reference computes Blinn-Phong specular per vertex and carries it
    // through COLOR1.  MMDLightColor is the Maya host mapping of both the
    // reference light diffuse and light specular terms.
    output.specular = float3(0.0f, 0.0f, 0.0f);
    if (Shininess > 0.0)
    {
        float3 lightDir = -normalize(MMDLightDirection);
        float3 halfVector = normalize(normalize(output.eye) + lightDir);
        float specularFactor = pow(
            saturate(dot(halfVector, output.worldNormal)), Shininess);
        output.specular = SpecularColor * MMDLightColor * specularFactor;
    }

    // Pass through UV
    output.texCoord0 = float2(input.texCoord0.x, 1.0 - input.texCoord0.y); // Flip V for Maya
    output.texCoord1 = input.texCoord1;
    output.vertexColor0 = input.vertexColor0;
    output.vertexColor1 = input.vertexColor1;

    // Calculate shadow coordinates
    output.shadowCoord = mul(worldPos, Light0Matrix);

    return output;
}

//--------------------------------------------------------------------------------------
// Shared texture operations and reference Basic/BufferShadow composition
//--------------------------------------------------------------------------------------
float4 SampleMainTexture(float2 uv)
{
    float4 texColor = float4(1.0, 1.0, 1.0, 1.0);
    if (HasMainTexture != 0)
    {
        texColor = MainTexture.Sample(LinearSampler, uv);
        texColor = texColor * MainTextureMultiply + MainTextureAdd;
    }
    return texColor;
}

float3 SampleSphereTexture(float2 uv)
{
    if (HasSphereTexture == 0)
        return float3(1.0, 1.0, 1.0);

    float4 sphereSample = SphereTexture.Sample(LinearSampler, uv);
    return (sphereSample * SphereTextureMultiply + SphereTextureAdd).rgb;
}

float3 SampleToonTexture(VS_OUTPUT input)
{
    float3 lightDir = -normalize(MMDLightDirection);
    float NdotL = dot(input.worldNormal, lightDir);
    float toonV = 0.5f - NdotL * 0.5f;
    float4 toonSample = ToonTexture.Sample(
        ToonSampler, float2(0.0f, toonV));
    return (toonSample * ToonTextureMultiply + ToonTextureAdd).rgb;
}

float3 ComputeBasicColor(VS_OUTPUT input, out float opacity)
{
    // Reference Basic_PS order: base -> main texture -> sphere -> toon ->
    // interpolated vertex specular.  The base was composed in MainVS using
    // the Maya PMX flat-material adapter above.
    float4 texColor = SampleMainTexture(input.texCoord0);
    float3 surfaceColor = input.baseColor.rgb * texColor.rgb;

    if (SphereMode == 1 && HasSphereTexture != 0)
    {
        surfaceColor *= SampleSphereTexture(input.sphereUV);
    }
    else if (SphereMode == 2 && HasSphereTexture != 0)
    {
        surfaceColor += SampleSphereTexture(input.sphereUV);
    }

    if (HasToonTexture != 0)
    {
        surfaceColor *= SampleToonTexture(input);
    }

    surfaceColor += input.specular;
    opacity = texColor.a * DiffuseColorA * Opacity;
    clip(opacity - 0.003);

    // Keep Maya's existing projected-shadow scalar as the final adapter for
    // the ordinary Basic composition.  Native self shadow supplies its own
    // visibility to ComputeBufferShadowColor below.
    float shadow = CalculateShadow(input.shadowCoord, Light0ShadowMap);
    return surfaceColor * shadow;
}

float3 ComputeBufferShadowColor(VS_OUTPUT input, float visibility,
                                bool insideProjection, out float opacity)
{
    // Reference BufferShadow_PS keeps independent lit/shadow chains.  Both
    // start from the same host-composed flat base and receive the main and
    // sphere texture operations before the shadow-only toon multiplier.
    float4 texColor = SampleMainTexture(input.texCoord0);
    float3 litColor = input.baseColor.rgb * texColor.rgb;
    float3 shadowColor = input.baseColor.rgb * texColor.rgb;

    if (SphereMode == 1 && HasSphereTexture != 0)
    {
        float3 sphereColor = SampleSphereTexture(input.sphereUV);
        litColor *= sphereColor;
        shadowColor *= sphereColor;
    }
    else if (SphereMode == 2 && HasSphereTexture != 0)
    {
        float3 sphereColor = SampleSphereTexture(input.sphereUV);
        litColor += sphereColor;
        shadowColor += sphereColor;
    }

    // Specular is part of the lit chain before the final reference lerp.
    litColor += input.specular;

    opacity = texColor.a * DiffuseColorA * Opacity;
    clip(opacity - 0.003);

    // BufferShadow_PS returns the complete lit chain outside the projected
    // region.  This also prevents a toon ramp from being introduced there.
    if (!insideProjection)
        return litColor;

    if (HasToonTexture != 0)
    {
        // The reference shadow chain uses MaterialToon, the representative
        // dark-end color, rather than the per-pixel Basic toon ramp.
        float4 toonShadowSample = ToonTexture.Sample(
            ToonSampler, float2(0.0f, 1.0f));
        float3 MaterialToon = (
            toonShadowSample * ToonTextureMultiply + ToonTextureAdd).rgb;
        shadowColor *= MaterialToon;
        float3 lightDir = -normalize(MMDLightDirection);
        visibility = min(
            saturate(dot(input.worldNormal, lightDir) * 3.0f), visibility);
    }

    return lerp(shadowColor, litColor, visibility);
}

float3 ComputeMmdLitColorWithSelfShadow(VS_OUTPUT input, out float opacity,
                                      bool selfShadowEnabled,
                                      float selfShadowVisibility,
                                      bool insideSelfShadow)
{
    if (!selfShadowEnabled)
        return ComputeBasicColor(input, opacity);

    return ComputeBufferShadowColor(
        input, selfShadowVisibility, insideSelfShadow, opacity)
        * CalculateShadow(input.shadowCoord, Light0ShadowMap);
}

float3 ComputeMmdLitColor(VS_OUTPUT input, out float opacity)
{
    return ComputeBasicColor(input, opacity);
}

float4 MainPS(VS_OUTPUT input) : SV_TARGET
{
    float opacity = 0.0f;
    float3 litColor = ComputeMmdLitColor(input, opacity);

    // Product materials decode the gamma-space MMD result to linear; the view
    // transform re-encodes it to sRGB under CM-on.
    return float4(SrgbToLinear(litColor), opacity);
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
    // linear for Maya's CM-on view transform.
    return float4(SrgbToLinear(EdgeColorRGB), EdgeColorA);
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
// alpha-blended main pass. The MMD queue owns material/submesh order;
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
