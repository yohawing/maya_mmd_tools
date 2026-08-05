// Opt-in MMD self-shadow caster pass for the Maya Viewport 2.0 RenderOverride.
//
// This effect is intentionally limited to the plugin-owned R32F/D32 target
// pair.  It writes the rasterized DirectX viewport depth value to the R32F
// color attachment and to SV_Depth.  It does not sample a shadow map, shade
// a receiver, or claim MMD self-shadow parity.

// Maya does not expose the semantic-only WorldViewProjection parameter to a
// Python MShaderInstance callback reliably on an offscreen target.  The
// RenderOverride binds this plain matrix once per draw from MFrameContext.
float4x4 CasterWorldViewProjection;

struct CasterVertexInput
{
    float3 position : POSITION;
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
