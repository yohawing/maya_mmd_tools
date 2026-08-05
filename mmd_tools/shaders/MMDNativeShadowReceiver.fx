// Maya DX11 receiver-composition diagnostic for MMD Tools.
//
// This effect intentionally declares the native shadow texture and matrix as
// plain parameters.  Maya rejects a manual setParameter call on parameters
// carrying SHADOWMAP/SHADOWMAPMATRIX semantics, while the render override has
// already obtained those resources from the active-light API.  The pass mirrors
// the production untextured MMD main path; texture/toon resource handoff remains
// a separate Oracle slice.

float4x4 World : World<string UIWidget = "None";>;
float4x4 WorldInverseTranspose : WorldInverseTranspose<string UIWidget = "None";>;
float4x4 WorldViewProjection : WorldViewProjection<string UIWidget = "None";>;
float3 ViewPosition : ViewPosition<string UIWidget = "None";>;

float3 DiffuseColorRGB = {0.8f, 0.8f, 0.8f};
float DiffuseColorA = 1.0f;
float3 AmbientColor = {0.3f, 0.3f, 0.3f};
float3 SpecularColor = {0.5f, 0.5f, 0.5f};
float Shininess = 20.0f;
float Opacity = 1.0f;

bool UseShadows = false;
float ShadowStrength = 1.0f;
float ShadowBias = 0.01f;
float3 FixedLightDirection = {0.5f, -1.0f, 0.5f};
float3 FixedLightColor = {1.0f, 1.0f, 1.0f};

float3 SrgbToLinear(float3 color)
{
    color = max(color, 0.0f);
    float3 low = color / 12.92f;
    float3 high = pow((color + 0.055f) / 1.055f, 2.4f);
    return lerp(high, low, step(color, 0.04045f));
}

// These are bound explicitly by NativeShadowReceiverRender.  Do not add Maya
// shadow semantics here: the native resource is already selected in Python.
Texture2D Light0ShadowMap;
float4x4 Light0Matrix;

SamplerState ShadowSampler
{
    Filter = MIN_MAG_MIP_POINT;
    AddressU = Border;
    AddressV = Border;
    BorderColor = float4(1.0f, 1.0f, 1.0f, 1.0f);
};

struct VS_INPUT
{
    float3 position : POSITION;
    float3 normal : NORMAL;
};

struct VS_OUTPUT
{
    float4 position : SV_POSITION;
    float3 worldPosition : TEXCOORD0;
    float3 worldNormal : NORMAL;
    float4 shadowCoord : TEXCOORD1;
};

VS_OUTPUT MainVS(VS_INPUT input)
{
    VS_OUTPUT output = (VS_OUTPUT)0;
    float4 worldPosition = mul(float4(input.position, 1.0f), World);
    output.position = mul(float4(input.position, 1.0f), WorldViewProjection);
    output.worldPosition = worldPosition.xyz;
    output.worldNormal = normalize(mul(input.normal, (float3x3)WorldInverseTranspose));
    output.shadowCoord = mul(worldPosition, Light0Matrix);
    return output;
}

float ShadowFactor(float4 shadowCoord)
{
    if (!UseShadows || shadowCoord.w <= 0.0f)
        return 1.0f;
    float3 shadowPosition = shadowCoord.xyz / shadowCoord.w;
    if (shadowPosition.x < -1.0f || shadowPosition.x > 1.0f ||
        shadowPosition.y < -1.0f || shadowPosition.y > 1.0f ||
        shadowPosition.z < 0.0f || shadowPosition.z > 1.0f)
        return 1.0f;
    float2 shadowUv = shadowPosition.xy * 0.5f + 0.5f;
    shadowUv.y = 1.0f - shadowUv.y;
    float shadowDepth = Light0ShadowMap.Sample(ShadowSampler, shadowUv).r;
    float currentDepth = shadowPosition.z - ShadowBias / shadowCoord.w;
    float visible = currentDepth > shadowDepth ? 0.0f : 1.0f;
    return lerp(1.0f, visible, saturate(ShadowStrength));
}

float4 MainPS(VS_OUTPUT input) : SV_TARGET
{
    float3 normal = normalize(input.worldNormal);
    float3 viewDirection = normalize(ViewPosition - input.worldPosition);
    float3 lightDirection = -normalize(FixedLightDirection);
    float3 lightColor = FixedLightColor;
    float shadow = ShadowFactor(input.shadowCoord);
    float3 materialBase = saturate(DiffuseColorRGB * lightColor + AmbientColor);
    float3 diffuse = materialBase * shadow;
    float3 specular = float3(0.0f, 0.0f, 0.0f);
    if (Shininess > 0.0f)
    {
        float3 halfVector = normalize(lightDirection + viewDirection);
        float specularFactor = pow(saturate(dot(normal, halfVector)), Shininess);
        specular = SpecularColor * specularFactor * lightColor * shadow;
    }
    float3 litColor = diffuse + specular;
    float opacity = Opacity * DiffuseColorA;
    clip(opacity - 0.003f);
    return float4(SrgbToLinear(litColor), opacity);
}

RasterizerState CullNone
{
    CullMode = None;
};

BlendState NoBlend
{
    BlendEnable[0] = FALSE;
};

DepthStencilState OverlayDepth
{
    DepthEnable = TRUE;
    DepthWriteMask = ZERO;
    DepthFunc = LESS_EQUAL;
};

technique11 MMDNativeShadowReceiver
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(OverlayDepth, 0);
    }
}
