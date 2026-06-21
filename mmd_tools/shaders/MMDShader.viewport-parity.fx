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

// Per-frame parameters. Maya's dx11Shader uniform builder binds global effect
// variables more reliably than explicit cbuffers.
float4x4 View       : View<string UIWidget = "None";>;
float4x4 ViewInv    : ViewInverse<string UIWidget = "None";>;
float4x4 Projection : Projection<string UIWidget = "None";>;
float4x4 ViewProjection : ViewProjection<string UIWidget = "None";>;
float3 ViewPosition : ViewPosition<string UIWidget = "None";>;
float2 ScreenSize : ViewportPixelSize<string UIWidget = "None";>;

// Per-object parameters
float4x4 World               : World<string UIWidget = "None";>;
float4x4 WorldInverse        : WorldInverse<string UIWidget = "None";>;
float4x4 WorldInverseTranspose : WorldInverseTranspose<string UIWidget = "None";>;
float4x4 WorldViewProjection : WorldViewProjection<string UIWidget = "None";>;

// Material parameters
float3 DiffuseColorRGB<
    string UIGroup = "Material";
    string UIName = "Diffuse Color";
    string UIWidget = "Color";
    int UIOrder = 200;
> = {0.8f, 0.8f, 0.8f};

float DiffuseColorA<
    string UIGroup = "Material";
    string UIName = "Diffuse Alpha";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 1.0;
    float UIStep = 0.001;
    int UIOrder = 201;
> = 1.0f;

float3 SpecularColor<
    string UIGroup = "Material";
    string UIName = "Specular Color";
    string UIWidget = "Color";
    int UIOrder = 202;
> = {0.5f, 0.5f, 0.5f};

float Shininess<
    string UIGroup = "Material";
    string UIName = "Shininess";
    string UIWidget = "Slider";
    float UIMin = 1.0;
    float UIMax = 100.0;
    int UIOrder = 203;
> = 20.0f;

float3 AmbientColor<
    string UIGroup = "Material";
    string UIName = "Ambient Color";
    string UIWidget = "Color";
    int UIOrder = 204;
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
float3 EdgeColorRGB<
    string UIGroup = "Outline";
    string UIName = "Edge Color";
    string UIWidget = "Color";
    int UIOrder = 400;
> = {0.0f, 0.0f, 0.0f};

float EdgeColorA<
    string UIGroup = "Outline";
    string UIName = "Edge Alpha";
    string UIWidget = "Slider";
    float UIMin = 0.0;
    float UIMax = 1.0;
    float UIStep = 0.001;
    int UIOrder = 401;
> = 1.0f;

float EdgeSize<
    string UIGroup = "Outline";
    string UIName = "Edge Size";
    string UIWidget = "Slider";
    float UIMin = 0.0f;
    float UIMax = 10.0f;
    int UIOrder = 402;
> = 1.0f;

// Sphere mapping
int SphereMode<
    string UIGroup = "Effects";
    string UIName = "Sphere Mode";
    string UIFieldNames = "None:Multiply:Add:SubTexture";
    float UIMin = 0;
    float UIMax = 3;
    float UIStep = 1;
    int UIOrder = 500;
> = 0;

int HasMainTexture<
    string UIGroup = "Textures";
    string UIName = "Has Main Texture";
    float UIMin = 0;
    float UIMax = 1;
    float UIStep = 1;
    int UIOrder = 510;
> = 0;

int HasSphereTexture<
    string UIGroup = "Textures";
    string UIName = "Has Sphere Texture";
    float UIMin = 0;
    float UIMax = 1;
    float UIStep = 1;
    int UIOrder = 511;
> = 0;

int HasToonTexture<
    string UIGroup = "Textures";
    string UIName = "Has Toon Texture";
    float UIMin = 0;
    float UIMax = 1;
    float UIStep = 1;
    int UIOrder = 512;
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

// Light parameters
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

int UseFixedLight<
    string UIGroup = "Lighting";
    string UIName = "Use Fixed Light";
> = 0;

float3 FixedLightDirection<
    string UIGroup = "Lighting";
    string UIName = "Fixed Light Direction";
> = {0.5f, -1.0f, 0.5f};

float3 FixedLightColor<
    string UIGroup = "Lighting";
    string UIName = "Fixed Light Color";
> = {1.0f, 1.0f, 1.0f};

float4x4 Light0Matrix : SHADOWMAPMATRIX
<
    string Object = "Light 0";
    string UIWidget = "None";
    int UIOrder = 1000;
>;

//--------------------------------------------------------------------------------------
// Vertex Shader Input/Output Structures
//--------------------------------------------------------------------------------------
struct VS_INPUT
{
    float3 position     : POSITION;
    float2 texCoord0    : TEXCOORD0;
    float2 texCoord1    : TEXCOORD1;
    float4 vertexColor0 : COLOR0;
    float4 vertexColor1 : COLOR1;
    float3 normal       : NORMAL;
    float3 tangent      : TANGENT;
    float3 binormal     : BINORMAL;
};

struct VS_OUTPUT
{
    float4 position     : SV_POSITION;
    float2 texCoord0    : TEXCOORD0;
    float2 texCoord1    : TEXCOORD1;
    float4 vertexColor0 : TEXCOORD2;
    float4 vertexColor1 : TEXCOORD3;
    float3 worldPosition : TEXCOORD4;
    float4 shadowCoord  : TEXCOORD5;
    float3 worldNormal  : NORMAL;
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
    float4 localPos = float4(input.position, 1.0);
    float4 worldPos = mul(localPos, World);
    output.position = mul(localPos, WorldViewProjection);
    output.worldPosition = worldPos.xyz;

    // Transform normal
    output.worldNormal = normalize(mul(input.normal, (float3x3)WorldInverseTranspose));

    // Pass through UV
    output.texCoord0 = float2(input.texCoord0.x, 1.0 - input.texCoord0.y); // Flip V for Maya
    output.texCoord1 = input.texCoord1;
    output.vertexColor0 = input.vertexColor0;
    output.vertexColor1 = input.vertexColor1;

    // Calculate shadow coordinates
    output.shadowCoord = mul(worldPos, Light0Matrix);

    return output;
}

//--------------------------------------------------------------------------------------
// Main Pass Pixel Shader
//--------------------------------------------------------------------------------------
float4 MainPS(VS_OUTPUT input) : SV_TARGET
{
    // Normalize inputs
    float3 normal = normalize(input.worldNormal);
    float3 viewDir = normalize(ViewPosition - input.worldPosition);
    float3 lightDir = (Light0Type == 4) ? -normalize(Light0Dir) : normalize(Light0Pos - input.worldPosition);
    float3 lightColor = Light0Color;
    if (UseFixedLight != 0)
    {
        lightDir = -normalize(FixedLightDirection);
        lightColor = FixedLightColor;
    }

    // Sample textures
    float4 texColor = float4(1.0, 1.0, 1.0, 1.0);
    if (HasMainTexture != 0)
    {
        texColor = MainTexture.Sample(LinearSampler, input.texCoord0);
    }
    float4 baseColor = texColor * float4(DiffuseColorRGB, DiffuseColorA);

    // Calculate shadow
    float shadow = CalculateShadow(input.shadowCoord, Light0ShadowMap);

    // Ambient light
    float3 ambient = AmbientColor * baseColor.rgb;

    // Diffuse light (Half-Lambert for toon shading)
    float NdotL = dot(normal, lightDir);
    float halfLambert = NdotL * 0.5 + 0.5;
    
    // Sample toon texture. MMD toon ramps are conventionally vertical strips;
    // use RGB as the ramp color rather than a scalar red-channel factor.
    float rampCoord = saturate(halfLambert);
    float3 toonColor = rampCoord.xxx;
    if (HasToonTexture != 0)
    {
        rampCoord = saturate(halfLambert * 0.5 + 0.25);
        toonColor = ToonTexture.Sample(ToonSampler, float2(0.5, 1.0 - rampCoord)).rgb;
    }
    float3 diffuse = lightColor * toonColor * shadow;

    // Specular light
    float3 halfVec = normalize(lightDir + viewDir);
    float NdotH = saturate(dot(normal, halfVec));
    float specFactor = pow(NdotH, Shininess);
    float3 specular = SpecularColor * specFactor * lightColor * shadow;

    // Sphere mapping
    float3 sphereColor = float3(1.0, 1.0, 1.0);
    if (SphereMode > 0 && HasSphereTexture != 0)
    {
        float3 sphereNormal = normalize(mul(float4(normal, 0.0), View).xyz);
        float2 sphereUV;
        sphereUV.x = sphereNormal.x * 0.35 + 0.5;
        sphereUV.y = sphereNormal.y * -0.35 + 0.5;
        sphereColor = SphereTexture.Sample(LinearSampler, sphereUV).rgb;
    }

    // Combine lighting
    float3 litColor = diffuse * baseColor.rgb + specular;
    if (HasToonTexture == 0)
    {
        litColor += ambient;
    }

    // Apply sphere map
    if (SphereMode == 1 && HasSphereTexture != 0) // Multiply
        litColor *= sphereColor;
    else if (SphereMode == 2 && HasSphereTexture != 0) // Add
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

    float4 localPos = float4(input.position, 1.0);
    float4 worldPos = mul(localPos, World);
    float4 clipPos = mul(localPos, WorldViewProjection);

    float3 worldNormal = normalize(mul(input.normal, (float3x3)WorldInverseTranspose));
    float3 viewNormal = normalize(mul(worldNormal, (float3x3)View));
    float2 screenNormal = viewNormal.xy;
    screenNormal /= max(length(screenNormal), 1.0e-5);

    float2 safeScreenSize = max(ScreenSize, float2(1.0, 1.0));
    clipPos.xy += screenNormal / (safeScreenSize * 0.5) * EdgeSize * clipPos.w;

    output.position = clipPos;
    output.worldPosition = worldPos.xyz;

    return output;
}

//--------------------------------------------------------------------------------------
// Edge Pass Pixel Shader
//--------------------------------------------------------------------------------------
float4 EdgePS(VS_OUTPUT input) : SV_TARGET
{
    return float4(EdgeColorRGB, EdgeColorA);
}

//--------------------------------------------------------------------------------------
// Rasterizer States
//--------------------------------------------------------------------------------------
RasterizerState CullFront
{
    CullMode = Front;
};

RasterizerState CullBack
{
    CullMode = Back;
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

DepthStencilState EnableDepthNoWrite
{
    DepthEnable = TRUE;
    DepthWriteMask = ZERO;
    DepthFunc = LESS_EQUAL;
};

//--------------------------------------------------------------------------------------
// Techniques
//--------------------------------------------------------------------------------------

// Standard technique with edge rendering
technique11 MMDTechnique<
    int isTransparent = 0;
>
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepthNoWrite, 0);
    }
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

technique11 MMDTechniqueTransparent<
    int isTransparent = 1;
>
{
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepthNoWrite, 0);
    }
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(AlphaBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

// Technique without edge for performance
technique11 MMDTechniqueNoEdge<
    int isTransparent = 0;
>
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(NoBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepth, 0);
    }
}

technique11 MMDTechniqueNoEdgeTransparent<
    int isTransparent = 1;
>
{
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullNone);
        SetBlendState(AlphaBlend, float4(0.0, 0.0, 0.0, 0.0), 0xFFFFFFFF);
        SetDepthStencilState(EnableDepthNoWrite, 0);
    }
}
