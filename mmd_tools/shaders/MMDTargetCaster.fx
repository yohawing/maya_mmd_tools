// Opt-in MMD self-shadow caster pass for the Maya Viewport 2.0 RenderOverride.
//
// This effect is intentionally limited to the plugin-owned RGBA8/D24S8
// target pair.  Maya's production MMD render items accept a scalar color
// output here, so the color attachment carries a normalized-depth diagnostic
// in its R channel while D24S8 retains the full depth precision.  It does not
// sample a shadow map, shade a receiver, or claim MMD self-shadow parity.

// Maya does not expose the semantic-only WorldViewProjection parameter to a
// Python MShaderInstance callback reliably on an offscreen target.  The
// RenderOverride binds this plain matrix once per draw from MFrameContext.
float4x4 World : World<string UIWidget = "None";>;
float4x4 WorldInverseTranspose : WorldInverseTranspose<string UIWidget = "None";>;
float4x4 WorldViewProjection : WorldViewProjection<string UIWidget = "None";>;
float3 ViewPosition : ViewPosition<string UIWidget = "None";>;
float4x4 CasterWorldViewProjection;

struct CasterVertexInput
{
    float3 position : POSITION;
    // Keep the same vertex requirements as the imported MMD material.  Maya's
    // built-in dx11Shader render items reject a replacement instance whose
    // input layout is narrower than their standard shaded layout.
    float2 texCoord0 : TEXCOORD0;
    float2 texCoord1 : TEXCOORD1;
    float4 vertexColor0 : COLOR0;
    float4 vertexColor1 : COLOR1;
    float3 normal : NORMAL;
    float3 tangent : TANGENT;
    float3 binormal : BINORMAL;
};

struct CasterVertexOutput
{
    float4 position : SV_POSITION;
};

CasterVertexOutput CasterVS(CasterVertexInput input)
{
    CasterVertexOutput output;
    output.position = mul(float4(input.position, 1.0f), CasterWorldViewProjection);
    return output;
}

// The stock dx11Shader node binds Maya effect semantics itself.  Keep this
// second entry point separate from the callback-bound pass above so the
// diagnostic can test that ownership boundary without changing the normal
// operation-wide shader path.
CasterVertexOutput CasterStockVS(CasterVertexInput input)
{
    CasterVertexOutput output;
    output.position = mul(float4(input.position, 1.0f), WorldViewProjection);
    return output;
}

struct CasterPixelOutput
{
    float color : SV_TARGET0;
    float depth : SV_DEPTH;
};

CasterPixelOutput CasterPS(CasterVertexOutput input)
{
    CasterPixelOutput output;
    // SV_POSITION is in viewport space for the pixel stage, so z is already
    // the rasterized depth in the DirectX [0, 1] range.  Keep that value in
    // the documented clear range before writing both targets; dividing by w
    // here would incorrectly apply the clip-space transform a second time.
    const float depth = saturate(input.position.z);
    output.color = depth;
    output.depth = depth;
    return output;
}

technique11 MmdToolsR32FCaster
{
    pass CasterPass
    {
        SetVertexShader(CompileShader(vs_5_0, CasterVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, CasterPS()));
    }
}

technique11 MmdToolsR32FCasterNodeSwap
{
    pass CasterPass
    {
        SetVertexShader(CompileShader(vs_5_0, CasterStockVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, CasterPS()));
    }
}
