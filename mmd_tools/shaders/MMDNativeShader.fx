// Native VP2 shader for the ordered render override.  It includes MMDShader.fx
// for the shared material/lighting contract and adds body, outline, and caster
// techniques with receiver-side self-shadow.

#include "MMDShader.fx"

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

// The ordered render override binds its same-frame R32F caster target to body
// shaders for receiver-side self-shadow comparison.
Texture2D NativeCasterDepthTexture<
    string UIName = "Native Caster Depth";
    string UIWidget = "None";
    string ResourceType = "2D";
    int mipmaplevels = NumberOfMipMaps;
>;

// Product self-shadow state. Zero preserves the ordinary native material.
int NativeSelfShadowMode<string UIWidget = "None";> = 0;

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

float CasterCutoutPS(VS_OUTPUT input) : SV_TARGET
{
    float alpha = 1.0;
    if (HasMainTexture != 0)
    {
        float2 uv = float2(input.texCoord0.x, 1.0 - input.texCoord0.y);
        alpha = MainTexture.Sample(LinearSampler, uv).a;
        alpha = alpha * MainTextureMultiply.a + MainTextureAdd.a;
    }
    clip(alpha * DiffuseColorA * Opacity - 0.003);
    return input.position.z;
}

float EvaluateNativeSelfShadow(float3 worldPosition, out bool inside)
{
    float4 clipPosition = mul(float4(worldPosition, 1.0), CasterLightViewProjection);
    inside = false;
    if (clipPosition.w <= 1.0e-6)
        return 1.0;
    float3 ndc = clipPosition.xyz / clipPosition.w;
    float2 uv = float2(ndc.x * 0.5 + 0.5, 0.5 - ndc.y * 0.5);
    if (any(uv < 0.0) || any(uv > 1.0) || ndc.z < 0.0 || ndc.z > 1.0)
        return 1.0;
    inside = true;
    float depth = NativeCasterDepthTexture.SampleLevel(ShadowSampler, uv, 0).r;
    if (depth >= 1.0 - 1.0e-6)
        return 1.0;
    float delta = max(ndc.z - (depth - CasterDepthBias), 0.0);
    // Documented MMD receiver ramps; formal image parity still needs the
    // independent mode/range oracle recorded in the reference matrix.
    float ramp = NativeSelfShadowMode == 2 ? 8000.0 * uv.y : 1500.0;
    return 1.0 - saturate(delta * ramp - 0.3);
}

// Native body pixel shader with receiver-side self-shadow.
float4 NativeMainPS(VS_OUTPUT input) : SV_TARGET
{
    float opacity = 0.0f;
    float3 litColor;
    if (NativeSelfShadowMode != 0)
    {
        bool inside;
        float visibility = EvaluateNativeSelfShadow(input.worldPosition, inside);
        litColor = ComputeMmdLitColorWithSelfShadow(input, opacity, true,
                                                   visibility, inside);
    }
    else
        litColor = ComputeMmdLitColor(input, opacity);

    float3 outputColor = NativeSrgbOutput != 0
                             ? litColor
                             : SrgbToLinear(litColor);
    return float4(outputColor, opacity);
}

float4 NativeEdgePS(VS_OUTPUT input) : SV_TARGET
{
    // All techniques contain the edge pass. A material opts out in shader
    // space by setting EdgeSize to zero, avoiding separate NoEdge techniques.
    clip(EdgeSize - 1.0e-5);
    // Native items render into the CM-off sRGB target and therefore keep the
    // authored edge value directly when NativeSrgbOutput is enabled.
    float3 outputColor = NativeSrgbOutput != 0
                             ? EdgeColorRGB
                             : SrgbToLinear(EdgeColorRGB);
    return float4(outputColor, EdgeColorA);
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
        SetPixelShader(CompileShader(ps_5_0, NativeMainPS()));
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
        SetPixelShader(CompileShader(ps_5_0, NativeMainPS()));
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
        SetPixelShader(CompileShader(ps_5_0, NativeMainPS()));
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
        SetPixelShader(CompileShader(ps_5_0, NativeMainPS()));
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
        SetPixelShader(CompileShader(ps_5_0, NativeEdgePS()));
        SetRasterizerState(CullBack);
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
        SetPixelShader(CompileShader(ps_5_0, NativeEdgePS()));
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
        SetPixelShader(CompileShader(ps_5_0, NativeEdgePS()));
        SetRasterizerState(CullBack);
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
        SetPixelShader(CompileShader(ps_5_0, NativeEdgePS()));
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

technique11 MMDNativeCasterCutout
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, CasterVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, CasterCutoutPS()));
        SetRasterizerState(CullNone);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}
