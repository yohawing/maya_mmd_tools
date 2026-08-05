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
    // Scan a fixed low-resolution grid so a 4x4 output does not depend on
    // the caster landing at the center texel.  The caster target is 2048x2048
    // and this 16x16 diagnostic grid is sufficient to witness the real
    // non-clear R32F data for the PMX fixture without a full-screen readback.
    float value = 1.0f;
    [unroll]
    for (int y = 0; y < 16; ++y)
    {
        [unroll]
        for (int x = 0; x < 16; ++x)
        {
            const float2 uv = (float2(x, y) + 0.5f) / 16.0f;
            value = min(value, MmdToolsR32FTarget.SampleLevel(
                MmdToolsR32FSampler, uv, 0));
        }
    }
    // Keep the one-minus transform so the separate output remains visibly
    // changed from its clear value even when the caster map is all clear.
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
