// Opt-in native Maya shadow-resource binding probe.
//
// The render operation binds Maya's kShadowMap/kShadowViewProj resources to
// this plugin-owned shader through an MDrawContext callback.  It only writes a
// tiny R32F diagnostic target and does not replace imported MMD materials or
// claim receiver/self-shadow parity.

Texture2D MayaShadowMap;
SamplerState MayaShadowSampler
{
    Filter = MIN_MAG_MIP_POINT;
    AddressU = Border;
    AddressV = Border;
    BorderColor = float4(1.0f, 1.0f, 1.0f, 1.0f);
};

float4x4 WorldViewProjection : WorldViewProjection<string UIWidget = "None";>;
float4x4 MayaShadowViewProj : MayaShadowViewProj<string UIWidget = "None";>;
bool MayaShadowEnabled : MayaShadowEnabled<string UIWidget = "None";> = false;

struct ProbeVertexInput
{
    float4 position : POSITION;
    float2 uv : TEXCOORD0;
};

struct ProbeVertexOutput
{
    float4 position : SV_POSITION;
    float2 uv : TEXCOORD0;
};

ProbeVertexOutput ProbeVS(ProbeVertexInput input)
{
    ProbeVertexOutput output;
    output.position = mul(input.position, WorldViewProjection);
    output.uv = input.uv;
    return output;
}

float ProbePS(ProbeVertexOutput input) : SV_TARGET0
{
    if (!MayaShadowEnabled)
        return 1.0f;
    // The output target is intentionally tiny. Scan a deterministic grid so
    // a valid shadow map is witnessed even when the quad's center texel lands
    // in an unoccupied part of the native map.
    float value = 1.0f;
    [unroll]
    for (int y = 0; y < 16; ++y)
    {
        [unroll]
        for (int x = 0; x < 16; ++x)
        {
            const float2 uv = (float2(x, y) + 0.5f) / 16.0f;
            value = min(value, MayaShadowMap.SampleLevel(MayaShadowSampler, uv, 0).r);
        }
    }
    return value;
}

technique11 MmdToolsNativeShadowBindingProbe
{
    pass NativeShadowBindingProbePass
    {
        SetVertexShader(CompileShader(vs_5_0, ProbeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, ProbePS()));
    }
}
