// MMD-style Shader for Maya Viewport 2.0 (DirectX 11)
// Based on MikuMikuDance rendering style with Maya integration
// Version: 2.0

#define NumberOfMipMaps 0
#define PI 3.1415926

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

// Per-frame parameters
cbuffer UpdatePerFrame : register(b0)
{
    float4x4 View       : View<string UIWidget = "None";>;
    float4x4 ViewInv    : ViewInverse<string UIWidget = "None";>;
    float4x4 Projection : Projection<string UIWidget = "None";>;
    float4x4 ViewProjection : ViewProjection<string UIWidget = "None";>;
    float3 ViewPosition : ViewPosition<string UIWidget = "None";>;
}

// Per-object parameters
cbuffer UpdatePerObject : register(b1)
{
    float4x4 World               : World<string UIWidget = "None";>;
    float4x4 WorldInverse        : WorldInverse<string UIWidget = "None";>;
    float4x4 WorldInverseTranspose : WorldInverseTranspose<string UIWidget = "None";>;
    float4x4 WorldViewProjection : WorldViewProjection<string UIWidget = "None";>;

    // Material parameters
    float4 DiffuseColor<
        string UIGroup = "Material";
        string UIName = "Diffuse Color";
        string UIWidget = "Color";
        int UIOrder = 200;
    > = {0.8f, 0.8f, 0.8f, 1.0f};

    float3 SpecularColor<
        string UIGroup = "Material";
        string UIName = "Specular Color";
        string UIWidget = "Color";
        int UIOrder = 201;
    > = {0.5f, 0.5f, 0.5f};

    float Shininess<
        string UIGroup = "Material";
        string UIName = "Shininess";
        string UIWidget = "Slider";
        float UIMin = 1.0;
        float UIMax = 100.0;
        int UIOrder = 202;
    > = 20.0f;

    float3 AmbientColor<
        string UIGroup = "Material";
        string UIName = "Ambient Color";
        string UIWidget = "Color";
        int UIOrder = 203;
    > = {0.3f, 0.3f, 0.3f};

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
    float4 EdgeColor<
        string UIGroup = "Outline";
        string UIName = "Edge Color";
        string UIWidget = "Color";
        int UIOrder = 400;
    > = {0.0f, 0.0f, 0.0f, 1.0f};

    float EdgeSize<
        string UIGroup = "Outline";
        string UIName = "Edge Size";
        string UIWidget = "Slider";
        float UIMin = 0.0f;
        float UIMax = 10.0f;
        int UIOrder = 401;
    > = 1.0f;

    // Sphere mapping
    int SphereMode<
        string UIGroup = "Effects";
        string UIName = "Sphere Mode";
        string UIFieldNames = "None:Multiply:Add";
        float UIMin = 0;
        float UIMax = 2;
        float UIStep = 1;
        int UIOrder = 500;
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
}

// Light parameters
cbuffer UpdateLights : register(b2)
{
    int Light0Type : LIGHTTYPE
    <
        string Object = "Light 0";
        string UIName = "Light 0 Type";
        string UIWidget = "None";
        int UIOrder = 1000;
    > = 0;

    float3 Light0Pos : POSITION
    <
        string Object = "Light 0";
        string UIName = "Light 0 Position";
        string Space = "World";
        string UIWidget = "None";
        int UIOrder = 1000;
    > = {100.0f, 100.0f, 100.0f};

    float3 Light0Color : LIGHTCOLOR
    <
        string Object = "Light 0";
        string UIName = "Light 0 Color";
        string UIWidget = "None";
        int UIOrder = 1000;
    > = {1.0f, 1.0f, 1.0f};

    float3 Light0Dir : DIRECTION
    <
        string Object = "Light 0";
        string UIName = "Light 0 Direction";
        string Space = "World";
        string UIWidget = "None";
        int UIOrder = 1000;
    > = {0.0f, -1.0f, 0.0f};

    float4x4 Light0Matrix : SHADOWMAPMATRIX
    <
        string Object = "Light 0";
        string UIWidget = "None";
        int UIOrder = 1000;
    >;
}

//--------------------------------------------------------------------------------------
// Vertex Shader Input/Output Structures
//--------------------------------------------------------------------------------------
struct VS_INPUT
{
    float3 Position : POSITION;
    float3 Normal   : NORMAL;
    float2 UV       : TEXCOORD0;
    float4 Tangent  : TANGENT0;
    float3 Binormal : BINORMAL0;
};

struct VS_OUTPUT
{
    float4 Position     : SV_POSITION;
    float2 UV           : TEXCOORD0;
    float3 Normal       : NORMAL;
    float3 WorldPos     : TEXCOORD1;
    float4 ShadowCoord  : TEXCOORD2;
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
    float4 worldPos = mul(float4(input.Position, 1.0), World);
    output.Position = mul(worldPos, ViewProjection);
    output.WorldPos = worldPos.xyz;

    // Transform normal
    output.Normal = normalize(mul(input.Normal, (float3x3)WorldInverseTranspose));

    // Pass through UV
    output.UV = float2(input.UV.x, 1.0 - input.UV.y); // Flip V for Maya

    // Calculate shadow coordinates
    output.ShadowCoord = mul(worldPos, Light0Matrix);

    return output;
}

//--------------------------------------------------------------------------------------
// Main Pass Pixel Shader
//--------------------------------------------------------------------------------------
float4 MainPS(VS_OUTPUT input) : SV_TARGET
{
    // Normalize inputs
    float3 normal = normalize(input.Normal);
    float3 viewDir = normalize(ViewPosition - input.WorldPos);
    float3 lightDir = (Light0Type == 4) ? -normalize(Light0Dir) : normalize(Light0Pos - input.WorldPos);

    // Sample textures
    float4 texColor = MainTexture.Sample(LinearSampler, input.UV);
    float4 baseColor = texColor * DiffuseColor;

    // Calculate shadow
    float shadow = CalculateShadow(input.ShadowCoord, Light0ShadowMap);

    // Ambient light
    float3 ambient = AmbientColor * baseColor.rgb;

    // Diffuse light (Half-Lambert for toon shading)
    float NdotL = dot(normal, lightDir);
    float halfLambert = NdotL * 0.5 + 0.5;
    
    // Sample toon texture
    float toonFactor = ToonTexture.Sample(ToonSampler, float2(halfLambert, 0.5)).r;
    float3 diffuse = Light0Color * toonFactor * shadow;

    // Specular light
    float3 halfVec = normalize(lightDir + viewDir);
    float NdotH = saturate(dot(normal, halfVec));
    float specFactor = pow(NdotH, Shininess);
    float3 specular = SpecularColor * specFactor * Light0Color * shadow;

    // Sphere mapping
    float3 sphereColor = float3(1.0, 1.0, 1.0);
    if (SphereMode > 0)
    {
        float3 reflectVec = reflect(-viewDir, normal);
        float2 sphereUV;
        sphereUV.x = reflectVec.x * 0.5 + 0.5;
        sphereUV.y = reflectVec.y * -0.5 + 0.5;
        sphereColor = SphereTexture.Sample(LinearSampler, sphereUV).rgb;
    }

    // Combine lighting
    float3 litColor = ambient + diffuse * baseColor.rgb + specular;

    // Apply sphere map
    if (SphereMode == 1) // Multiply
        litColor *= sphereColor;
    else if (SphereMode == 2) // Add
        litColor += sphereColor;

    // Apply opacity
    float opacity = baseColor.a * Opacity;

    return float4(litColor, opacity);
}

//--------------------------------------------------------------------------------------
// Edge Pass Vertex Shader
//--------------------------------------------------------------------------------------
VS_OUTPUT EdgeVS(VS_INPUT input)
{
    VS_OUTPUT output = (VS_OUTPUT)0;

    // Calculate object scale
    float3 objectScale = float3(
        length(World._m00_m10_m20),
        length(World._m01_m11_m21),
        length(World._m02_m12_m22)
    );

    // Transform to view space for consistent edge width
    float4 viewPos = mul(float4(input.Position, 1.0), mul(World, View));
    
    // Scale edge by view distance
    float edgeScale = saturate(abs(viewPos.z) * 0.01) * 0.1 / objectScale;
    
    // Extrude along normal
    float3 extrudedPos = input.Position + input.Normal * EdgeSize * edgeScale;
    
    // Transform to clip space
    float4 worldPos = mul(float4(extrudedPos, 1.0), World);
    output.Position = mul(worldPos, ViewProjection);
    output.WorldPos = worldPos.xyz;

    return output;
}

//--------------------------------------------------------------------------------------
// Edge Pass Pixel Shader
//--------------------------------------------------------------------------------------
float4 EdgePS(VS_OUTPUT input) : SV_TARGET
{
    return EdgeColor;
}

//--------------------------------------------------------------------------------------
// Rasterizer States
//--------------------------------------------------------------------------------------
RasterizerState CullFront
{
    CullMode = Back;
};

RasterizerState CullBack
{
    CullMode = Front;
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
DepthStencilState EnableDepth
{
    DepthEnable = TRUE;
    DepthWriteMask = ALL;
    DepthFunc = LESS_EQUAL;
};

//--------------------------------------------------------------------------------------
// Techniques
//--------------------------------------------------------------------------------------

// Standard technique with edge rendering
technique11 MMDTechnique<
    bool overridesDrawState = true;
    int isTransparent = 3;
    string transparencyTest = "Opacity < 1.0";
>
{
    // Main model pass
    pass MainPass<
        string drawContext = "colorPass";
    >
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullBack);
        SetBlendState(AlphaBlend, float4(0.0f, 0.0f, 0.0f, 0.0f), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
    
    // Edge rendering pass
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0f, 0.0f, 0.0f, 0.0f), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

// Technique without edge for performance
technique11 MMDTechniqueNoEdge<
    bool overridesDrawState = true;
    int isTransparent = 3;
    string transparencyTest = "Opacity < 1.0";
>
{
    pass MainPass<
        string drawContext = "colorPass";
    >
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(AlphaBlend, float4(0.0f, 0.0f, 0.0f, 0.0f), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}