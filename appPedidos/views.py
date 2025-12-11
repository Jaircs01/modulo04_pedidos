from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from .models import Pedido
from .serializers import PedidoSerializer
from django.shortcuts import render
from django.db.models import Q
from django import forms
import requests
from django.conf import settings
from rest_framework.decorators import api_view


def monitor(request):
    return render(request, 'monitor.html')

def notificar_modulo3_pedido_listo(pedido: Pedido):
    """ Se envía una notificacion al Módulo 3 cuando el pedido pasa a LISTO.
    
    Se Envía:

    - id del pedido
    - mesa
    - cliente
    - orden (descripcion)
    - fecha de creación
    - estado (LISTO)
    - hora en la que llegó a LISTO """


    url = getattr(settings, "MODULO3_WEBHOOK_URL", None)
    if not url:
        # Si no hay URL configurada, no hacemos nada
        return

    payload = {
        "id_pedido": pedido.id,
        "mesa": pedido.mesa,
        "cliente": pedido.cliente,
        "orden": pedido.descripcion,
        "fecha_creacion": pedido.fecha_creacion.isoformat() if pedido.fecha_creacion else None,
        "estado": "LISTO",
        "hora_listo": pedido.fecha_actualizacion.isoformat() if pedido.fecha_actualizacion else None,
    }

    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        # En un sistema real, aquí se podría registrar en logs.
        print(f"[MÓDULO 4] Error notificando al Módulo 3: {e}")


class PedidoViewSet(viewsets.ModelViewSet):
    # Ocultar ENTREGADOS en la pantalla principal
    queryset = Pedido.objects.all().order_by('-fecha_creacion')
    serializer_class = PedidoSerializer

    # Transiciones válidas
    transiciones = {
        "URGENTE": ["EN_PREPARACION"],
        "CREADO": ["URGENTE", "EN_PREPARACION"],
        "EN_PREPARACION": ["LISTO"],
        "LISTO": ["ENTREGADO"],
        "ENTREGADO": []
    }

    def update(self, request, *args, **kwargs):
        """
        Sobrescribe update para:
        - Validar transiciones de estado
        - Y cuando el estado cambie a LISTO, notificar al Módulo 3.
        """
        instance = self.get_object()
        estado_anterior = instance.estado
        nuevo_estado = request.data.get("estado", None)

        # Si no se manda "estado" en el body, dejamos que DRF maneje el update normal
        if nuevo_estado is None:
            return super().update(request, *args, **kwargs)

        # Validar transición
        if nuevo_estado not in self.transiciones.get(instance.estado, []):
            return Response(
                {"error": f"No puedes pasar de {instance.estado} a {nuevo_estado}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Actualizamos el estado
        instance.estado = nuevo_estado
        instance.save()

        # Si ahora quedó LISTO y antes no lo estaba, notificamos al Módulo 3
        if estado_anterior != "LISTO" and instance.estado == "LISTO":
            notificar_modulo3_pedido_listo(instance)

        return Response(PedidoSerializer(instance).data)


    # -------- FILTRO POR ESTADO --------
    @action(detail=False, methods=['get'])
    def filtrados(self, request):
        estado = request.query_params.get('estado', 'CREADO').upper()
        pedidos = Pedido.objects.filter(estado=estado)
        return Response({
            "estado": estado,
            "cantidad": pedidos.count(),
            "resultados": PedidoSerializer(pedidos, many=True).data
        })

    # -------- SOLO ENTREGADOS --------
    @action(detail=False, methods=['get'])
    def entregados(self, request):
        pedidos = Pedido.objects.filter(estado="ENTREGADO")
        return Response(PedidoSerializer(pedidos, many=True).data)

@api_view(["POST"])
def recibir_pedido_modulo3(request):

    """
    Endpoint de entrada para pedidos provenientes del Módulo 3.

    """

    data = request.data

    # Datos esperados (ejemplo):
    # {
    #   "id_pedido": "uuid-del-modulo3",
    #   "mesa": 7,
    #   "cliente": "Juan Pérez",
    #   "orden": "2x Hamburguesa, 1x Coca-Cola",
    #   "fecha_creacion": "2025-12-11T18:30:00Z",
    #   "estado": "CREADO"
    # }

    mesa = data.get("mesa")
    cliente = data.get("cliente")
    orden = data.get("orden")

    if mesa is None or not cliente or not orden:
        return Response(
            {"error": "Faltan campos obligatorios: mesa, cliente u orden."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Creamos el pedido en tu BD del Módulo 4
    pedido = Pedido.objects.create(
        mesa=mesa,
        cliente=cliente,
        descripcion=orden,
        estado=Pedido.EstadoPedido.CREADO
    )

    serializer = PedidoSerializer(pedido)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


def detalle_pedido(request, pedido_id):
    pedido = get_object_or_404(Pedido, pk=pedido_id)

    ahora = timezone.now()

    # Si el pedido está ENTREGADO, tomamos fecha_actualizacion como hora de salida
    if pedido.estado == Pedido.EstadoPedido.ENTREGADO:
        hora_salida = pedido.fecha_actualizacion
        delta = pedido.fecha_actualizacion - pedido.fecha_creacion
    else:
        hora_salida = None
        delta = ahora - pedido.fecha_creacion

    total_segundos = int(delta.total_seconds())
    minutos = total_segundos // 60
    segundos = total_segundos % 60
    tiempo_en_cocina = f"{minutos} min {segundos} s"

    context = {
        "pedido": pedido,
        "tiempo_en_cocina": tiempo_en_cocina,
        "hora_salida": hora_salida,
    }
    return render(request, "detalle_pedido.html", context)

def administrar_pedidos(request):
    # Eliminar pedido (POST)
    if request.method == "POST":
        eliminar_id = request.POST.get("eliminar_id")
        if eliminar_id:
            Pedido.objects.filter(pk=eliminar_id).delete()
            return redirect("administrar_pedidos")

    # Búsqueda (GET)
    consulta = request.GET.get("q", "").strip()
    pedidos = Pedido.objects.all().order_by("-fecha_creacion")

    if consulta:
        # Buscar por id (numérico), nombre de cliente o número de pedido (id)
        filtro = Q(cliente__icontains=consulta) | Q(descripcion__icontains=consulta)
        if consulta.isdigit():
            filtro |= Q(id=int(consulta))
        pedidos = pedidos.filter(filtro)
    else:
        # Si no hay búsqueda, mostrar los más recientes (por ejemplo 10)
        pedidos = pedidos[:10]

    contexto = {
        "pedidos": pedidos,
        "consulta": consulta,
    }
    return render(request, "administrar_pedidos.html", contexto)


class FormPedido(forms.ModelForm):
    class Meta:
        model = Pedido
        fields = ["mesa", "cliente", "descripcion", "estado"]
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 3}),
        }

def editar_pedido(request, pedido_id):
    pedido = get_object_or_404(Pedido, pk=pedido_id)

    if request.method == "POST":
        formulario = FormPedido(request.POST, instance=pedido)
        if formulario.is_valid():
            formulario.save()
            return redirect("administrar_pedidos")
    else:
        formulario = FormPedido(instance=pedido)

    contexto = {
        "pedido": pedido,
        "formulario": formulario,
    }
    return render(request, "editar_pedido.html", contexto)

def historial_pedidos(request):
    """
    Historial de la jornada:
    - Hora de ingreso
    - Hora de salida
    - Tiempo total
    """
    hoy = timezone.localdate()
    pedidos = Pedido.objects.filter(
        fecha_creacion__date=hoy
    ).order_by('fecha_creacion')

    registros = []
    for p in pedidos:
        hora_ingreso = p.fecha_creacion
        # Consideramos hora de salida cuando está ENTREGADO
        hora_salida = (
            p.fecha_actualizacion
            if p.estado == Pedido.EstadoPedido.ENTREGADO
            else None
        )
        registros.append({
            "pedido": p,
            "hora_ingreso": hora_ingreso,
            "hora_salida": hora_salida,
        })

    contexto = {"registros": registros}
    return render(request, "historial_pedidos.html", contexto)