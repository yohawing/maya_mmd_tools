// Diagnostic-only VP2 effect for R32F MRenderTarget parameter binding.
//
// This effect is never inserted into a render operation.  The RenderOverride
// uses it solely to call MShaderInstance.setParameter with a plugin-owned
// MRenderTarget, then releases the instance before releasing that target.
// It must not be used as a receiver/self-shadow implementation.

Texture2D MmdToolsR32FTarget;
SamplerState MmdToolsR32FSampler
{
    Filter = MIN_MAG_MIP_POINT;
    AddressU = Clamp;
    AddressV = Clamp;
};

struct ProbeVertexOutput
{
    float4 position : SV_POSITION;
    float2 uv : TEXCOORD0;
};

ProbeVertexOutput ProbeVS(uint vertex_id : SV_VertexID)
{
    const float2 positions[3] = {
        float2(-1.0f, -1.0f), float2(-1.0f, 3.0f), float2(3.0f, -1.0f)
    };
    ProbeVertexOutput output;
    output.position = float4(positions[vertex_id], 0.0f, 1.0f);
    output.uv = positions[vertex_id] * 0.5f + 0.5f;
    return output;
}

float4 ProbePS(ProbeVertexOutput input) : SV_TARGET
{
    return MmdToolsR32FTarget.SampleLevel(MmdToolsR32FSampler, input.uv, 0);
}

technique11 MmdToolsR32FTargetBindingProbe
{
    pass ProbePass
    {
        SetVertexShader(CompileShader(vs_5_0, ProbeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, ProbePS()));
    }
}
