// MMD-style Shader for Maya Viewport 2.0 (DirectX 11)
// File: MMDShader.fx

//--------------------------------------------------------------------------------------
// Constant Buffers
//--------------------------------------------------------------------------------------

// Per-frame global properties
cbuffer GlobalConstants : register(b0)
{
    float4x4 View       : View;
    float4x4 Projection : Projection;
    
    float3 LightDirection : LIGHTDIRECTION;
    float3 LightColor     : LIGHTCOLOR;
    float3 CameraPosition : CAMERAPOSITION;
};

// Per-object properties
cbuffer MaterialConstants : register(b1)
{
    float4x4 World               : World;
    float4x4 WorldInverseTranspose : WorldInverseTranspose;

    float4 DiffuseColor;
    float3 SpecularColor;
    float Shininess;
    float3 AmbientColor;
    float4 EdgeColor;
    float EdgeSize;
    int SphereMode; // 0: None, 1: Multiply, 2: Add
};

//--------------------------------------------------------------------------------------
// Texture and Sampler Objects
//--------------------------------------------------------------------------------------

Texture2D MainTexture : register(t0);
Texture2D SphereTexture : register(t1);
Texture2D ToonTexture : register(t2);
// ShadowMap would be t3, but we'll handle that later.

SamplerState LinearSampler : register(s0)
{
    Filter = MIN_MAG_MIP_LINEAR;
    AddressU = Wrap;
    AddressV = Wrap;
};

// Toon textures often require clamping at the edges.
SamplerState ToonSampler : register(s1)
{
    Filter = MIN_MAG_MIP_LINEAR;
    AddressU = Clamp;
    AddressV = Clamp;
};


//--------------------------------------------------------------------------------------
// Vertex Shader Input/Output Structures
//--------------------------------------------------------------------------------------

struct VS_INPUT
{
    float4 Position : POSITION;
    float3 Normal   : NORMAL;
    float2 UV       : TEXCOORD0;
};

struct VS_OUTPUT
{
    float4 Position     : SV_POSITION;
    float2 UV           : TEXCOORD0;
    float3 Normal       : NORMAL;
    float3 WorldPos     : TEXCOORD1;
};


//--------------------------------------------------------------------------------------
// Vertex and Pixel Shaders (Implementations will be added)
//--------------------------------------------------------------------------------------

// --- Main Pass Shaders ---
VS_OUTPUT MainVS(VS_INPUT input)
{
    VS_OUTPUT output = (VS_OUTPUT)0;

    // Transform position to world space and then to projection space
    float4 worldPosition = mul(input.Position, World);
    output.Position = mul(worldPosition, View);
    output.Position = mul(output.Position, Projection);

    // Pass through the UV coordinates
    output.UV = input.UV;

    // Transform normal to world space for lighting calculations
    output.Normal = normalize(mul(input.Normal, (float3x3)WorldInverseTranspose));

    // Pass world position to pixel shader
    output.WorldPos = worldPosition.xyz;

    return output;
}

float4 MainPS(VS_OUTPUT input) : SV_TARGET
{
    // Normalize inputs from vertex shader
    float3 normal = normalize(input.Normal);
    float3 viewDir = normalize(CameraPosition - input.WorldPos);
    float3 lightDir = normalize(LightDirection);

    // 1. Get base color from texture and material
    float4 baseColor = MainTexture.Sample(LinearSampler, input.UV) * DiffuseColor;

    // 2. Ambient light
    float3 ambient = AmbientColor;

    // 3. Diffuse light (Toon Shading)
    float dotNL = saturate(dot(normal, lightDir));
    float toonFactor = ToonTexture.Sample(ToonSampler, float2(0.5, dotNL)).r;
    float3 diffuse = LightColor * toonFactor;

    // 4. Specular light
    float3 halfVec = normalize(lightDir + viewDir);
    float dotNH = saturate(dot(normal, halfVec));
    float specFactor = pow(dotNH, Shininess);
    float3 specular = SpecularColor * specFactor * LightColor;

    // 5. Sphere map
    float3 reflectionVec = reflect(-viewDir, normal);
    float2 sphereUV = 0.5 * (normalize(reflectionVec).xy + 1.0);
    sphereUV.y = 1.0 - sphereUV.y; // Flip Y for DirectX
    float3 sphereColor = SphereTexture.Sample(LinearSampler, sphereUV).rgb;

    // 6. Combine lighting
    float3 litColor = (ambient + diffuse) * baseColor.rgb + specular;

    // 7. Apply sphere map
    if (SphereMode == 1) // Multiply
    {
        litColor *= sphereColor;
    }
    else if (SphereMode == 2) // Add
    {
        litColor += sphereColor;
    }

    // Final color
    return float4(litColor, DiffuseColor.a);
}


// --- Edge Pass Shaders ---
VS_OUTPUT EdgeVS(VS_INPUT input)
{
    VS_OUTPUT output = (VS_OUTPUT)0;

    // 1. Transform position and normal to world space
    float4 worldPosition = mul(input.Position, World);
    float3 worldNormal = mul(input.Normal, (float3x3)WorldInverseTranspose);
    worldNormal = normalize(worldNormal);

    // 2. Extrude vertex along the normal in world space
    worldPosition.xyz += worldNormal * EdgeSize;

    // 3. Transform to projection space
    output.Position = mul(worldPosition, View);
    output.Position = mul(output.Position, Projection);

    return output;
}

float4 EdgePS(VS_OUTPUT input) : SV_TARGET
{
    // Implementation to follow
    return EdgeColor;
}


//--------------------------------------------------------------------------------------
// Rasterizer, Blend, and Depth States
//--------------------------------------------------------------------------------------

RasterizerState CullFront
{
    CullMode = BACK; // Swapped for Maya's winding order
};

RasterizerState CullBack
{
    CullMode = FRONT; // Swapped for Maya's winding order
};

BlendState DisableBlend
{
    BlendEnable[0] = FALSE;
};

DepthStencilState DefaultDepth
{
    DepthEnable = TRUE;
    DepthWriteMask = ALL;
};


//--------------------------------------------------------------------------------------
// Technique and Passes
//--------------------------------------------------------------------------------------

technique11 MMDTechnique
{
    // Pass 0: Edge Rendering
    pass EdgePass
    {
        SetVertexShader(CompileShader(vs_5_0, EdgeVS()));
        SetPixelShader(CompileShader(ps_5_0, EdgePS()));
        SetRasterizerState(CullFront);
        SetBlendState(DisableBlend, float4(0.0f, 0.0f, 0.0f, 0.0f), 0xFFFFFFFF);
        SetDepthStencilState(DefaultDepth, 0);
    }

    // Pass 1: Main Model Rendering
    pass MainPass
    {
        SetVertexShader(CompileShader(vs_5_0, MainVS()));
        SetPixelShader(CompileShader(ps_5_0, MainPS()));
        SetRasterizerState(CullBack);
        SetBlendState(DisableBlend, float4(0.0f, 0.0f, 0.0f, 0.0f), 0xFFFFFFFF);
        SetDepthStencilState(DefaultDepth, 0);
    }
}
