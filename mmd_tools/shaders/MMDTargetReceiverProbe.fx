// Diagnostic-only VP2 quad effect for sampling the plugin-owned caster target.
//
// MQuadRender supplies POSITION/TEXCOORD0 for its automatic screen quad.  The
// output is a separate R32F target so the input caster target is never bound as
// both an active output and a sampled resource.  This is a readback/binding
// probe, not MMD receiver shading or self-shadow composition.

Texture2D MmdToolsR32FTarget;
SamplerState MmdToolsR32FSampler
{
    Filter = MIN_MAG_MIP_POINT;
    AddressU = Clamp;
    AddressV = Clamp;
};

float4x4 WorldViewProjection : WorldViewProjection<string UIWidget = "None";>;

struct ReceiverVertexInput
{
    float4 position : POSITION;
    float2 uv : TEXCOORD0;
};

struct ReceiverVertexOutput
{
    float4 position : SV_POSITION;
    float2 uv : TEXCOORD0;
};

ReceiverVertexOutput ReceiverVS(ReceiverVertexInput input)
{
    ReceiverVertexOutput output;
    output.position = mul(input.position, WorldViewProjection);
    output.uv = input.uv;
    return output;
}

float ReceiverPS(ReceiverVertexOutput input) : SV_TARGET0
{
    // A single quad texel can land in the clear border of the current-camera
    // caster map.  Probe a small neighborhood so the diagnostic has a stable
    // dataflow witness, but keep the explicit one-minus transform so a drawn
    // quad changes the separate output even when the sampled map is clear.
    const float2 center = float2(0.5f, 0.5f);
    float value = MmdToolsR32FTarget.SampleLevel(MmdToolsR32FSampler, center, 0);
    value = min(value, MmdToolsR32FTarget.SampleLevel(
        MmdToolsR32FSampler, center + float2(-0.2f, -0.2f), 0));
    value = min(value, MmdToolsR32FTarget.SampleLevel(
        MmdToolsR32FSampler, center + float2(0.2f, -0.2f), 0));
    value = min(value, MmdToolsR32FTarget.SampleLevel(
        MmdToolsR32FSampler, center + float2(-0.2f, 0.2f), 0));
    value = min(value, MmdToolsR32FTarget.SampleLevel(
        MmdToolsR32FSampler, center + float2(0.2f, 0.2f), 0));
    return 1.0f - value;
}

technique11 MmdToolsR32FReceiverProbe
{
    pass ReceiverPass
    {
        SetVertexShader(CompileShader(vs_5_0, ReceiverVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, ReceiverPS()));
    }
}
