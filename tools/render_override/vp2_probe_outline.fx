// Capability probe only: object-space hull expansion, not PMX edge parity.
float4x4 WorldViewProjection : WorldViewProjection;
float Width = 0.08;
struct Input { float3 position : POSITION; float3 normal : NORMAL; };
float4 vertexMain(Input input) : SV_POSITION
{
    return mul(float4(input.position + normalize(input.normal) * Width, 1), WorldViewProjection);
}
float4 pixelMain() : SV_TARGET { return float4(0, 1, 0, 1); }
// Maya's DX11 mesh winding uses the opposite face from the D3D default.
RasterizerState Hull { CullMode = Back; };
DepthStencilState Depth { DepthEnable = true; DepthWriteMask = Zero; DepthFunc = Less_Equal; };
BlendState Opaque { BlendEnable[0] = false; RenderTargetWriteMask[0] = 0x0F; };
technique11 Outline
{
    pass p0
    {
        SetVertexShader(CompileShader(vs_5_0, vertexMain()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, pixelMain()));
        SetRasterizerState(Hull);
        SetDepthStencilState(Depth, 0);
        SetBlendState(Opaque, float4(0, 0, 0, 0), 0xFFFFFFFF);
    }
}
